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
without changing a line of its own code. See [Gateway](docs/gateway.md).

### Does GLiNER2 need fine-tuning for German? No.

That verdict is specifically about PERSON — the only type this question was
ever built to answer (see `EvalReport.verdict()` in
`privaparse/evaluation/harness.py`). Measured on the German gold set in
`eval/gold/` — **124 documents, 33 with no PII at all** (a gold set with only
positives cannot see a false positive, which turns out to be exactly where
this configuration struggles) — scored at **21 enabled types, 31 labels**,
the catalogue as it ships today.

That is a larger gold set than the one this section quoted before: 91
documents, 76 supporting PERSON entities, measured 2026-08-10. Batches D and
E added 33 more documents afterward, every one of them containing PII —
which is exactly where a detector's misses hide, since a document with
nothing in it cannot produce a false negative. This section was regenerated
2026-08-14 against the grown set; the 91-document numbers are not restated
here, but the full old-versus-new comparison for every scored type is in
[docs/benchmarks/detection-quality.md](docs/benchmarks/detection-quality.md).

An earlier note in this README quoted PERSON at P 0.967 / R 0.983 / F1
0.975, measured under 3 labels on 38 documents, 10 of them negatives. That
number is real; it describes a narrower configuration, not the one below.
The real difference between the two runs is not whether negatives existed
— even at 3 labels, those 10 already caught two false positives
([docs/benchmarks/detection-quality.md](docs/benchmarks/detection-quality.md) — `de-009`'s "König von
Spanien", `de-026`'s "vier Personen") — it is how much surface a false
positive had to land on. At 35 labels, the same class of corpus catches
23.

The threshold was fixed *before* this run, same discipline as before:
fine-tuning is warranted if PERSON partial-match recall drops below 0.90 or
precision below 0.85.

| Type | Precision (partial) | Recall (partial) | F1 | Support |
| --- | ---: | ---: | ---: | ---: |
| PERSON | 0.969 | 0.960 | 0.964 | 99 |
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
| USERNAME | 0.750 | 1.000 | 0.857 | 3 |
| IP | 1.000 | 1.000 | 1.000 | 3 |
| DATE_OF_BIRTH | 0.667 | 1.000 | 0.800 | 4 |
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

**Seven types that had no gold data at all, and now do:** `DRIVERS_LICENSE`,
`LICENSE_NUMBER`, `ACCOUNT_NUMBER`, `ROUTING_NUMBER`, `CARD_EXPIRY`,
`CARD_CVV`, `SECRET`. Batches D and E gave each of them three gold entities
— thin, same caveat as the block above, but no longer zero — and the first
real measurement is not a flattering one:

| Type | Precision (partial) | Recall (partial) | F1 | Support |
| --- | ---: | ---: | ---: | ---: |
| DRIVERS_LICENSE | 1.000 | 1.000 | 1.000 | 3 |
| CARD_EXPIRY | 1.000 | 1.000 | 1.000 | 3 |
| SECRET | 1.000 | 1.000 | 1.000 | 3 |
| ACCOUNT_NUMBER | 0.429 | 1.000 | 0.600 | 3 |
| CARD_CVV | 0.167 | 0.333 | 0.222 | 3 |
| LICENSE_NUMBER | 1.000 | 0.000 | 0.000 | 3 |
| ROUTING_NUMBER | 1.000 | 0.000 | 0.000 | 3 |

Three of the seven are clean. ACCOUNT_NUMBER's precision and CARD_CVV's
precision and recall are weak. The other two are not weak, they are absent:
**LICENSE_NUMBER and ROUTING_NUMBER measured 0.000 recall — three gold
entities each, and the pipeline missed every one.** Zero gold coverage used
to mean the only failure mode visible was a false positive; that framing no
longer holds for either of these two, and it never was a guarantee that they
worked, only that nothing had checked. Now something has, and for these two
the answer is that they currently detect nothing.

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
[Install](docs/install.md)). `docs/benchmarks/detection-quality.md` now carries this exact
run — every one of the 21 enabled types, not only the four above — laid out
beside the 91-document numbers it superseded, with the direction of every
move stated per type.

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
(p50 21.4 ms) against the catalogue as it shipped for that measurement
session — 21 enabled types, 35 labels — versus **161.7 documents/second**
against the original 3-label configuration alone (person, email, phone).
Widening the catalogue costs roughly 3.5x throughput; that is the price of
the other 32 labels, not a regression. The 35-label figure is dated, not
wrong: this session ran 2026-08-10, a day before four labels that measured
zero true positives (`middle_name`, `secret`, `recovery_code`,
`sensitive_account_id` — see `docs/benchmarks/labels.md`) were removed from the
catalogue on 2026-08-11. The catalogue ships 31 labels today; this pair of
numbers has not been re-measured against that smaller set, so 35 is what
this specific run used, not a live description of what `privaparse doctor`
reports now. These two numbers are also not comparable to a throughput
figure from a different measurement session — GPU clock state, driver
version and thermal history all move the absolute value, which is exactly
why this pair was measured together rather than against an older number
from a different run.

Quality is identical across every device, dtype and batch size measured, on
both Linux and Windows.

Full matrix in [docs/benchmarks/throughput.md](docs/benchmarks/throughput.md) — that file has
not yet been regenerated for the 21-type catalogue and still describes the
3-label configuration; the two numbers above are current. If your own
numbers come out an order of magnitude worse, run `privaparse doctor` before
blaming the code: a laptop GPU held in its idle power state still reports
100 % utilisation and costs a factor of 14. That story is in
[docs/benchmarks/performance-notes.md](docs/benchmarks/performance-notes.md).

## Licence

Apache 2.0. See [LICENSE](LICENSE).
