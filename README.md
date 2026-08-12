# PrivaParse

A local privacy layer for text you want to send to an LLM. It detects personally
identifiable information, replaces it with deterministic placeholders, keeps the
mapping in a local database, and restores the original values in the model's
answer.

Nothing leaves the machine. Phase 1 calls no external service at all.

```
Hallo,                                  Hallo,
mein Name ist Max Mustermann.           mein Name ist [[PERSON_A1]].
Sie erreichen mich unter        ──▶     Sie erreichen mich unter
max@test.de                             [[EMAIL_A2]]
oder +49 170 1234567.                   oder [[PHONE_A3]].
```

The LLM only ever sees the right-hand side. `reverse` puts the left-hand side
back into whatever the model replies.

## Status

Phase 1: plain text and Markdown, a 25-type entity catalogue (21 enabled by
default — four ship disabled on measured evidence, see below), CLI and Python
library. No OCR, no PDF, no cloud models.

Phase 2 adds the **local gateway**: an OpenAI-compatible endpoint on
`127.0.0.1` that pseudonymises a request on the way out and restores the
answer on the way back, so any client that accepts a base URL gets PrivaParse
without changing a line of its own code. See [Gateway](#gateway).

### Does GLiNER2 need fine-tuning for German? No.

That verdict is specifically about PERSON — the only type this question was
ever built to answer (see `EvalReport.verdict()` in
`privaparse/evaluation/harness.py`). Measured on the German gold set in
`eval/gold/` — **91 documents, 33 with no PII at all** (a gold set with only
positives cannot see a false positive, which turns out to be exactly where
this configuration struggles) — scored at **21 enabled types, 35 labels**.

The catalogue ships **31** labels today, not 35. The four that went are the
four the per-label sweep found zero gold entities for
([docs/label-report.md](docs/label-report.md)), so their removal cannot move
any number in this section — a label that detected nothing contributed nothing
to detect. The scores below are still the scores of what ships; only the label
count differs, and it is stated here rather than quietly corrected because the
run itself was scored at 35.

An earlier note in this README quoted PERSON at P 0.967 / R 0.983 / F1
0.975, measured under 3 labels on 38 documents, 10 of them negatives. That
number is real; it describes a narrower configuration, not the one below.
The real difference between the two runs is not whether negatives existed
— even at 3 labels, those 10 already caught two false positives
([docs/eval-report.md](docs/eval-report.md) — `de-009`'s "König von
Spanien", `de-026`'s "vier Personen") — it is how much surface a false
positive had to land on. At 35 labels, the same class of corpus catches
23.

The threshold was fixed *before* this run, same discipline as before:
fine-tuning is warranted if PERSON partial-match recall drops below 0.90 or
precision below 0.85.

| Type | Precision (partial) | Recall (partial) | F1 | Support |
| --- | ---: | ---: | ---: | ---: |
| PERSON | 0.973 | 0.961 | 0.967 | 76 |
| EMAIL | 1.000 | 1.000 | 1.000 | 21 |
| PHONE | 0.818 | 1.000 | 0.900 | 18 |
| IBAN | 1.000 | 1.000 | 1.000 | 6 |

PERSON clears both floors comfortably — **fine-tuning not warranted.**
`fastino/gliner2-privacy-filter-PII-multi` is built on
`microsoft/mdeberta-v3-base`, so German is not a special case for it. EMAIL
and PHONE come from rules, not the model, so they are the control group:
EMAIL is clean, PHONE is not — see "One defect, two numbers" below for why.

**Nine more types, each resting on three gold entities — thin, and stated as
such rather than presented with PERSON's confidence:**

| Type | Precision (partial) | Recall (partial) | F1 | Support |
| --- | ---: | ---: | ---: | ---: |
| CARD | 1.000 | 1.000 | 1.000 | 3 |
| PASSPORT | 1.000 | 1.000 | 1.000 | 3 |
| USERNAME | 1.000 | 1.000 | 1.000 | 3 |
| IP | 1.000 | 1.000 | 1.000 | 3 |
| DATE_OF_BIRTH | 1.000 | 1.000 | 1.000 | 3 |
| ADDRESS | 0.750 | 1.000 | 0.857 | 3 |
| NATIONAL_ID | 0.750 | 1.000 | 0.857 | 3 |
| ACCOUNT_ID | 0.750 | 1.000 | 0.857 | 3 |
| POSTAL_CODE | 0.600 | 1.000 | 0.750 | 3 |

Support of 3 means one false positive is the entire gap: 0.750 is three
correct against one wrong, not a stable operating point the way PERSON's 76
or EMAIL's 21 are. Read this block as a first pass, not a verdict.

**One defect, two numbers.** All four of PHONE's false positives are all
four of TAX_ID's false negatives — the same defect, counted from both
ends, not two separate ones. TAX_ID reports P 1.000 / R 0.000 / F1 0.000
(support 4); the gold set is deliberately kept in a form that keeps this
gap visible rather than one shaped to avoid it (`eval/gold/de_gold_source.md`'s
Batch A note). The four missed Steuer-IDs reach PHONE by two different
routes. `de-047` keeps its Steuer-ID in the bare, ungrouped form
`generate_decidable()` actually produces, and the phone *backstop* — the
regex/`phonenumbers` rule, not the model — matches an unbroken eleven-digit
string as a German mobile number directly. `de-048` and `de-049` write
theirs the way a Finanzamt letter actually prints one, grouped in threes
(`XX XXX XXX XXX`); the backstop finds nothing there, but the *model*
labels all three `phone_number` instead — the per-label breakdown shows
exactly this, `phone_number` at 0 true positives and 3 false positives.
`phone_shape`, the validator that would otherwise veto a model guess that
is not actually phone-shaped, does not catch these three, because a
grouped twelve-digit number genuinely is phone-shaped — and TAX_ID's own
backstop (`vat_de`, matching a VAT-ID's `DE`-plus-nine-digits shape) does
not cover a Steuer-ID's shape at all, so nothing produces a competing exact
span for the model's guess to lose to. PHONE's 0.818 precision above and
TAX_ID's 0.000 recall are this one defect, read from opposite sides of the
same four entities.

The fix is known and deliberately deferred, not missed. A checksum-
validated TAX_ID backstop would turn it into an exact span, and the
existing rule that an exact span trims back an overlapping model guess
would resolve the three grouped cases on its own — the model's
`phone_number` label would simply lose to a genuine TAX_ID match. The bare
case needs a second rule that does not exist yet: when two *regex* spans of
different types compete for the same text — a new TAX_ID backstop against
the existing phone backstop, here — nothing currently prefers the one
backed by a checksum over the one that only checked shape. Both fixes wait
on a label-ablation study to measure whether `phone_number` is a
false-positive driver generally, not only here, since that answer could
change what the second rule needs to look like.

**Seven types with no gold data at all:** `DRIVERS_LICENSE`,
`LICENSE_NUMBER`, `ACCOUNT_NUMBER`, `ROUTING_NUMBER`, `CARD_EXPIRY`,
`CARD_CVV`, `SECRET`. None has a single gold entity in the corpus, so none
of them can register a true positive or a miss — the only thing that can
happen to any of them is a false positive. Reconstructing exact counts from
the precision/recall/support above (rounding to three decimals pins the
integers uniquely) shows the fourteen measured types account for 11 of the
corpus's 23 false positives; the other 12 belong to this group of seven,
with no way to say from this gold set which of the seven produced which
one. `privaparse eval` prints 1.000 or 0.000 for each of them depending on
whether it happened to be one that tripped — neither number means the type
works or doesn't; it means nothing was there to check it against.

**Overall, across all 21 enabled types on all 91 documents: 23 false
positives against 7 false negatives.** That ratio is the finding this gold
set exists to produce — at the precision a wider catalogue reports, the
failure mode is precision, not recall, and a corpus of nothing but real PII
could never have shown that, because every detection would have looked like
a hit.

**Four types ship disabled:** `CITY`, `REGION`, `COUNTRY`, `DATE`. Measured
false positives against zero true positives on this gold set — CITY 13,
REGION 4 (`state_or_region`), DATE 19 (`transaction_date` 14,
`expiration_date` 5). COUNTRY measured 0 false positives too, so its
disabling rests on judgement rather than this evidence — a country name
rarely identifies a person alone, but the corpus never gave the model one to
get wrong either way. Re-enabling any of them is `enabled: true` in a
`privaparse.entities.yaml` overlay; see the comment on each type in
`privaparse/app/entities.default.yaml` for the full reasoning.

Reproduce the detection table with `privaparse eval` (needs GLiNER2 — see
Install). `docs/eval-report.md` has not yet been regenerated for this
configuration and still describes the earlier, 3-label measurement — the
table above is the current one.

Those numbers are for short documents, which is what the gold set contains. On
documents long enough to be split into chunks, recall depends on the chunk
window — see below.

### Chunk size affects recall, not just speed

`PRIVAPARSE_CHUNK_CHARS` defaults to 512. It is not only a performance knob:
GLiNER scores candidate spans against the whole chunk, so a longer chunk means
more competing candidates and names that fall under the threshold. On a 7.2 KB
document, a 1500-character window scored PERSON recall 0.900 while 512 scored
0.950 at identical precision — and 512 was the faster of the two on GPU.

### Throughput

Two configurations, measured together in one sandbox session on the same
RTX 3060 so the ratio between them means something: **46.5 documents/second**
(p50 21.4 ms) against the shipped catalogue — 21 enabled types, 35 labels —
versus **161.7 documents/second** against the original 3-label configuration
alone (person, email, phone). Widening the catalogue costs roughly 3.5x
throughput; that is the price of the other 32 labels, not a regression.
These two numbers are not comparable to a throughput figure from a different
measurement session — GPU clock state, driver version and thermal history
all move the absolute value, which is exactly why this pair was measured
together rather than against an older number from a different run.

Quality is identical across every device, dtype and batch size measured, on
both Linux and Windows.

Full matrix in [docs/bench-report.md](docs/bench-report.md) — that file has
not yet been regenerated for the 21-type catalogue and still describes the
3-label configuration; the two numbers above are current. If your own
numbers come out an order of magnitude worse, run `privaparse doctor` before
blaming the code: a laptop GPU held in its idle power state still reports
100 % utilisation and costs a factor of 14. That story is in
[docs/performance-notes.md](docs/performance-notes.md).

## Install

```bash
python -m venv .venv
```

```bash
.venv/Scripts/pip install -e ".[dev]"
```

That gets you everything except the model backend — enough to run the full test
suite and the regex-only pipeline. For person detection you also need GLiNER2:

```bash
.venv/Scripts/pip install -e ".[model]"
```

That pulls the CPU build of PyTorch from PyPI. On a CUDA machine, swap it
afterwards — pick the index whose newest wheel matches the torch version you
already have, so the swap is CPU-for-CUDA and not also a version jump:

```bash
.venv/Scripts/pip install torch --index-url https://download.pytorch.org/whl/cu130 --force-reinstall
```

Swapping afterwards rather than installing from the CUDA index first is
deliberate: the PyTorch CDN is separate from PyPI and less reliable, so this
order means a bad connection costs you GPU speed rather than a working install.

If that download stalls — pip cannot resume a partial transfer, so a dropped
connection restarts the whole ~2 GB — fetch the wheel yourself and install the
file. `curl -C -` resumes:

```bash
curl -L -C - --retry 5 --retry-all-errors -o torch-cu130.whl "https://download-r2.pytorch.org/whl/cu130/torch-2.13.0%2Bcu130-cp312-cp312-win_amd64.whl"
```

Verify what you ended up with:

```bash
privaparse doctor
```

`doctor` prints the resolved device, dtype and model. If it says `device=cpu`
when you expected CUDA, the torch swap did not take.

### Switching between CPU and GPU

Nothing is compiled in. The device is read at engine construction:

```bash
PRIVAPARSE_DEVICE=cuda privaparse demo brief.md
```

```bash
PRIVAPARSE_DEVICE=cpu privaparse demo brief.md
```

`auto` picks CUDA when it is usable and CPU otherwise. An *explicit* `cuda` on a
machine without CUDA is an error, never a quiet downgrade.

Switching device also switches dtype: `quantize` and `compile` default to on for
CUDA and off for CPU, because fp16 and `torch.compile` pay off on a GPU and do
not on a CPU. Both can be pinned by hand (`PRIVAPARSE_QUANTIZE=false`) when you
want to compare like for like. `pytest -m model` includes a test asserting that
a CPU/GPU swap returns identical detections — being swappable is worth nothing
if the swap changes the answers.

**On Windows, `torch.compile` is unavailable** and is downgraded automatically.
The inductor backend needs Triton, which the PyTorch wheel does not ship on this
platform; without the check you get a `TritonMissing` crash on the first forward
pass, after the model has already loaded. Device and compile are treated
differently on purpose:

| | Unavailable → |
| --- | --- |
| **Device** | Error. Changes speed by ~20x and hides it. A contract. |
| **`torch.compile`** | Warn and continue in eager mode. Changes speed only, never the result. A hint. |

`privaparse doctor` shows `compile=off (triton is not installed …)` so the
downgrade is visible rather than silent. If you want compilation on Windows,
install a `triton-windows` build; the check picks it up automatically.

## Use

```bash
privaparse demo brief.md
```

`demo` runs the whole round trip and prints every stage — original, detections,
pseudonymised text, a mock LLM answer, and the restored result. It is the
fastest way to see whether the thing works on your documents.

The real workflow is two commands:

```bash
privaparse pseudonymize brief.md -o brief.pseudo.md
```

```bash
privaparse reverse antwort.md -o antwort.klar.md
```

`reverse` with no `--mapping` looks up the session that issued *every*
placeholder in the file. Partial coverage matches nothing, so this is
convenience rather than a way around the session boundary — a file carrying a
placeholder from a document you did not pseudonymise matches no session at all
and is refused.

Pass `--mapping <id>` when you want to pin a specific session, and
`--mapping-out brief.id` on `pseudonymize` to record the id at the time.

Other commands:

| Command | Purpose |
| --- | --- |
| `privaparse detect FILE --json` | Show what would be detected; writes nothing |
| `privaparse doctor` | Resolved device, dtype, model, vault path |
| `privaparse catalog show` | Resolved entity catalogue — types, thresholds, sources |
| `privaparse catalog validate [FILE]` | Check a catalogue for errors; changes nothing |
| `privaparse eval` | Score detection against the gold set (needs GLiNER2) |
| `privaparse bench` | Throughput and detection quality together (needs GLiNER2) |
| `privaparse vault stats` | Counts only — never prints stored values |
| `privaparse vault mappings` | Recorded sessions and their ids, for a lost `--mapping-out` |
| `privaparse serve` | Run the gateway on `127.0.0.1` |
| `privaparse run -- <cmd>` | Run a command with its OpenAI client pointed at the gateway |
| `privaparse gateway stats` | Counters from a running gateway — numbers only |

As a library:

```python
import privaparse

result = privaparse.pseudonymize(text)
answer = my_llm(result.text)
original = privaparse.reverse(result.mapping_id, answer).text
```

A long-running service should build one engine at startup instead, so the model
is loaded once:

```python
from privaparse.engine import PrivaParseEngine

engine = PrivaParseEngine()          # loads the model once
result = engine.pseudonymize(text)   # reuses it on every call
```

## Gateway

One command, and any client that accepts a base URL is going through
PrivaParse:

```bash
privaparse run -- claude
```

`run` starts a gateway if none is listening, sets `OPENAI_BASE_URL` in the
child's environment, and exits with the child's own exit code. Your
`OPENAI_API_KEY` is passed through untouched — the gateway forwards it to the
provider and stores no credential of its own.

Or run the gateway yourself and point things at it:

```bash
privaparse serve
```

```bash
OPENAI_BASE_URL=http://127.0.0.1:8787/v1 aider
```

Anything that reads `OPENAI_BASE_URL` or takes a `base_url` works: the OpenAI
Python and Node SDKs, Aider, Continue, Cline, LangChain, LlamaIndex, `curl`.
Endpoints are `/v1/chat/completions` (streaming and non-streaming, tool calls
included) and `/v1/models`.

### What it does to a request

Every text-bearing field is extracted, pseudonymised under **one mapping per
request**, and written back before anything is forwarded. One mapping matters:
the answer mixes placeholders from every message, and `reverse` resolves
against exactly one session.

**The request path fails closed.** A field the gateway has no rule for stops
the request with a 502 and nothing reaches the provider — a gateway that
forwards what it does not understand leaks the first time a client adopts a
new API feature, and does it silently. **The response path never aborts**: if
restoration fails you get the answer with placeholders standing, because at
that point the answer already exists and has been paid for.

Streamed answers are restored through a hold-back buffer, because
`[[PERSON_A1]]` arrives split across events. Streamed tool calls are collected
and emitted once complete, with their arguments parsed and re-serialised
rather than string-substituted — a restored name containing a quote would
otherwise produce arguments the client cannot parse.

Detection is cached per text block, keyed by the catalogue as well as the
text. A chat client resends its whole history every turn, so most blocks of
any request but the first were detected already.

### What it costs

Measured on an RTX 3060 against a coding-agent payload, with the provider
stubbed out — the provider's own latency is not PrivaParse's to report and
dwarfs these numbers anyway:

| Payload | First turn (cache empty) | Later turns (median) |
| ---: | ---: | ---: |
| 52 KB | 3.40 s | 0.27 s |
| 209 KB | 12.32 s | 1.07 s |

65 ms per KB cold, 5.2 ms per KB warm; the detection cache takes about 92 % off
a repeated turn. Both scale linearly with payload size, so they extrapolate.

The warm second is not detection — that is cached — it is span resolution and
the vault writes behind it, which every request does because a mapping is what
scopes an answer to one session. Full method, environment and caveats in
[docs/gateway-latency-report.md](docs/gateway-latency-report.md); the most
important caveat is that the measurement ran on four cores, so the warm figure
should improve on a workstation.

### Restoration puts real PII into the client

This is the thing to understand before deploying it anywhere but your own
machine. The gateway's whole job is to hand back **unredacted** answers. The
provider sees placeholders; the client sees real names.

So pointing a **server-side** client at this — Open WebUI, a shared LibreChat,
anything running on a host other than yours — places restored answers, with
real PII in them, on that host and in front of whoever else uses it. The tool
protects the hop to the model provider. It does nothing about the hop to the
client, because there is no such hop when the client is you.

For the same reason `serve` refuses any bind address that is not loopback. The
vault behind the gateway stores plaintext values and has no per-user access
control — it was built for one person on one machine — so a port the network
can reach is a vault the network can read back, whether or not the reader sent
the request that filled it. Reach it from another machine over an SSH tunnel,
not by binding a wider address.

### Known gaps

**Restoration needs the model to hand the placeholder back unchanged, and a
small model does not.** `reverse` matches exactly, so an answer containing
`[EMAIL_A1]` — one bracket pair short — restores nothing. Measured against
Qwen2.5-1.5B through vLLM: **0 of 6 placeholders came back byte-exact** in free
prose, mangled four different ways (bracket dropped, prose-ified to "Person
A1", omitted, quotes injected). Streamed *tool calls* were perfect across three
runs, because a schema field gets transcribed rather than composed.

Nothing leaks — the provider only ever saw the placeholder, and the user sees a
placeholder instead of their data, which is the safe direction. But it is
silent and it is total, and it hits hardest in exactly the configuration
`PRIVAPARSE_GATEWAY_UPSTREAM` exists to enable. Large models are far better at
verbatim reproduction; that has not been measured here. Full account in
[docs/gateway-model-fidelity-report.md](docs/gateway-model-fidelity-report.md),
reproduce with `eval/placeholder_fidelity.py` against your own model before
trusting a deployment.

**`TAX_ID` recall is 0.000 on the gold set, and the gateway inherits that
exactly.** Four German Steuer-IDs in the gold set are detected as PHONE
instead — the bare form is matched by the phone backstop, the grouped form is
labelled `phone_number` by the model, and nothing produces a competing exact
span. They are still pseudonymised, so nothing leaves in the clear; they are
pseudonymised as the *wrong type*, which means a client asking about tax IDs
gets a placeholder that reads as a phone number. The full account, and why the
fix is deferred rather than missed, is under
[One defect, two numbers](#does-gliner2-need-fine-tuning-for-german-no) above.
Anyone reading only this section should still meet it.

**Tool declarations are forwarded unscanned.** A `tools` block is the client's
own schema — function names, descriptions, a parameter shape — and
pseudonymising it would degrade the model's choice of tool while protecting
nobody. A client that writes a person into a tool *description* sends that
person to the provider.

**A streaming request cannot report an upstream error status.** The relay
starts as soon as the provider responds, so a provider-side error arrives in
the stream body rather than as an HTTP status.

**The deprecated `functions` / `function_call` fields are refused**, not
ignored. Same shape as `tools` and the same argument, but nothing is tested
against them.

### Docker

```bash
docker build --target full -t privaparse:full .
```

```bash
docker run --rm --network host -v privaparse-vault:/data privaparse:full
```

`full` bakes the model weights in and sets `PRIVAPARSE_OFFLINE=1`, so the
container never contacts the Hugging Face Hub. `--target slim` leaves the
weights out and downloads them on first use.

`--network host` is what makes the container's loopback bind reachable from
your host, and it is the only documented way in: publishing a port would mean
binding `0.0.0.0` inside the container, and the image has no way to ask for
that. Mount `/data` — the vault must outlive the container, or every past
answer becomes unrestorable.

Both targets are built and run under podman as part of testing; `full` was
verified to detect with `--network none`, which is the whole point of baking
the weights. One podman quirk: its default OCI image format silently drops
`HEALTHCHECK`, so `podman build --format docker` is needed if you want the
container healthcheck. Docker's builder keeps it either way. The image is
large — 5.3 GB slim, 6.6 GB full — and that is torch, not PrivaParse.

## Configuration

Every setting is an environment variable with the `PRIVAPARSE_` prefix, or a
CLI flag.

| Variable | Default | Notes |
| --- | --- | --- |
| `PRIVAPARSE_DEVICE` | `auto` | `auto`, `cpu`, `cuda`, `cuda:0` |
| `PRIVAPARSE_DETECTOR` | `hybrid` | `hybrid`, `gliner`, `regex` |
| `PRIVAPARSE_MODEL_ID` | `fastino/gliner2-privacy-filter-PII-multi` | |
| `PRIVAPARSE_DB_PATH` | `privaparse.db` | The vault |
| `PRIVAPARSE_ENTITIES` | *(discovered)* | Catalogue overlay path; auto-discovered from `./privaparse.entities.yaml` or `~/.config/privaparse/entities.yaml` otherwise |
| `PRIVAPARSE_THRESHOLD` | `0.5` | Fallback only — 21 of 25 catalogue types pin their own threshold and ignore this; `privaparse catalog show` lists which |
| `PRIVAPARSE_BATCH_SIZE` | `8` | Chunks per forward pass |
| `PRIVAPARSE_SCAN_CODE` | `false` | Also scan code blocks and URLs |
| `PRIVAPARSE_QUANTIZE` | on CUDA | fp16 weights |
| `PRIVAPARSE_COMPILE` | on CUDA | `torch.compile` |
| `PRIVAPARSE_GATEWAY_UPSTREAM` | `https://api.openai.com` | Origin the gateway forwards to — Azure, a local vLLM server, anything OpenAI-compatible. No `/v1` |
| `PRIVAPARSE_GATEWAY_CACHE` | `2048` | Text blocks the gateway keeps detection results for. `0` turns the cache off, so entries hold no entity values beyond the request |

`PRIVAPARSE_DEVICE=cuda` on a machine without CUDA **fails** rather than falling
back to CPU. A silent fallback is invisible in logs and shows up weeks later as
an unexplained slowdown.

## How it works

```
Markdown
  ├─ protect()          mask code fences, inline code, URLs (length-preserving)
  ├─ detect             GLiNER2 for names + regex/phonenumbers for email & phone
  ├─ merge              resolve overlaps, then sweep for missed repeats
  ├─ normalize          per type: E.164, lowercase, title-stripped
  ├─ resolve            vault lookup → stable placeholder
  └─ replace            character spans, back to front
```

Four design decisions worth knowing about:

**The vault is global.** A value gets the same placeholder in every document,
forever. `Max Mustermann` is `[[PERSON_A1]]` today and next year.

**Reversal is scoped to one session.** Because placeholders are stable they are
also guessable, so `reverse()` only resolves placeholders that *this* mapping
issued. Anything else is left in place and reported. Without that, writing
`[[PERSON_A47]]` into a document would read back a stranger's name.

**The suffix counter is shared across types.** You get `PERSON_A1`, `EMAIL_A2`,
`PHONE_A3` — not three `A1`s. Phase 1 does no cross-type linking, and a per-type
counter would imply a link that isn't there.

**Email and phone come from rules, not the model.** They have well-defined
syntax, so a model only adds variance. It also makes evaluation honest: if
email and phone score near 1.0 and person doesn't, the model is the problem
rather than the pipeline.

### The vault holds plaintext PII

`privaparse.db` accumulates every real name, address and number the tool has
ever seen. It is the most sensitive file the tool produces, it is not encrypted
in Phase 1, and it is in `.gitignore` for a reason. Every read and write goes
through a `ValueCipher` seam (`privaparse/database/cipher.py`) so encryption is
a one-class swap later rather than a migration.

### Markdown handling

Fenced code, inline code, HTML comments and URLs are masked before detection, so
a `user.email` in a code sample is not pseudonymised. Two deliberate exceptions:

- **YAML frontmatter is scanned** — `author:` fields carry real names.
- **`mailto:` targets are scanned** — a mailto link *is* an email address.

Indented code blocks are *not* protected: four-space indentation is ambiguous
with list continuation, and hiding a real name is the more expensive mistake.

Known limitation: a name inside a URL path (`https://firma.de/team/max-mustermann`)
is not detected while URLs are protected. Use `--scan-code` if that matters more
to you than false positives on domain names.

## Development

```bash
.venv/Scripts/python -m pytest
```

The default run skips everything that needs model weights and finishes in a
couple of seconds. To include the model tests:

```bash
.venv/Scripts/python -m pytest -m model
```

7/7 pass under the current 21-type catalogue, last checked in the same
sandbox session the numbers above came from.

Schema changes go through Alembic, because the vault holds data that cannot be
regenerated:

```bash
.venv/Scripts/alembic upgrade head
```

## Licence

Apache 2.0. See [LICENSE](LICENSE).
