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

Phase 1: plain text and Markdown, three entity types (person, email, phone), CLI
and Python library. No OCR, no PDF, no REST API, no cloud models — those are
Phase 2.

### Does GLiNER2 need fine-tuning for German? No.

Measured on a hand-annotated German gold set (38 documents — letters, emails,
file notes, minutes — with 60 person, 20 email and 18 phone entities, plus 10
documents containing no PII at all):

| Type | Precision (partial) | Recall (partial) | F1 |
| --- | ---: | ---: | ---: |
| PERSON | 0.967 | 0.983 | 0.975 |
| EMAIL | 1.000 | 1.000 | 1.000 |
| PHONE | 1.000 | 1.000 | 1.000 |

The threshold was fixed *before* the run: fine-tuning is warranted if PERSON
partial-match recall drops below 0.90 or precision below 0.85. Recall came in at
0.983 — one miss in sixty names, and that one is the salutation `Frau Doktor`
with no name attached. `fastino/gliner2-privacy-filter-PII-multi` is built on
`microsoft/mdeberta-v3-base`, so German is not a special case for it.

Reproduce with `privaparse eval`; full report in
[docs/eval-report.md](docs/eval-report.md).

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

On an RTX 3060 (fp16, `torch.compile`): **163 documents/second**, 5.8 ms p50 per
short document, ~53 KB/s sustained on long ones — flat from 4 KB to 58 KB.
Quality is identical across every device, dtype and batch size measured, on both
Linux and Windows.

Full matrix in [docs/bench-report.md](docs/bench-report.md). If your own numbers
come out an order of magnitude worse, run `privaparse doctor` before blaming the
code: a laptop GPU held in its idle power state still reports 100 % utilisation
and costs a factor of 14. That story is in
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
| `privaparse vault stats` | Counts only — never prints stored values |
| `privaparse vault mappings` | Recorded sessions and their ids, for a lost `--mapping-out` |

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

## Configuration

Every setting is an environment variable with the `PRIVAPARSE_` prefix, or a
CLI flag.

| Variable | Default | Notes |
| --- | --- | --- |
| `PRIVAPARSE_DEVICE` | `auto` | `auto`, `cpu`, `cuda`, `cuda:0` |
| `PRIVAPARSE_DETECTOR` | `hybrid` | `hybrid`, `gliner`, `regex` |
| `PRIVAPARSE_MODEL_ID` | `fastino/gliner2-privacy-filter-PII-multi` | |
| `PRIVAPARSE_DB_PATH` | `privaparse.db` | The vault |
| `PRIVAPARSE_THRESHOLD` | `0.5` | Score cutoff |
| `PRIVAPARSE_BATCH_SIZE` | `8` | Chunks per forward pass |
| `PRIVAPARSE_SCAN_CODE` | `false` | Also scan code blocks and URLs |
| `PRIVAPARSE_QUANTIZE` | on CUDA | fp16 weights |
| `PRIVAPARSE_COMPILE` | on CUDA | `torch.compile` |

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

Schema changes go through Alembic, because the vault holds data that cannot be
regenerated:

```bash
.venv/Scripts/alembic upgrade head
```

## Licence

Apache 2.0. See [LICENSE](LICENSE).
