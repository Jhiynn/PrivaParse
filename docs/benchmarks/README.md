# Benchmark reports

Every number under this directory is the output of a command listed next to
it, run on hardware described in the report itself. Nothing here is an
estimate or a target. Where a run fell short of a floor, mangled a
placeholder, or turned out to be measuring the wrong thing, the report says
so and stays published — a negative result is evidence too, and a reader
who wants to reproduce a claim needs the command that made it, not a
narrative about it.

## Reports

| Report | What it measures | Regenerate | Recorded |
| --- | --- | --- | --- |
| [detection-quality.md](detection-quality.md) | Precision, recall and F1 for every one of the shipped catalogue's 21 enabled types against the German gold set | `privaparse eval` | 2026-08-14 |
| [labels.md](labels.md) | Every one of the model's 42 labels, scored alone against the 124-document gold set | no single command — each label is run through the shipped pipeline with a one-label catalogue overlay (see "How this was measured" in the report) | 2026-08-11* |
| [throughput.md](throughput.md) | Latency and throughput across dtype/compile/batch-size configurations, quality scored in the same run | `privaparse bench --matrix --repeats 3` | 2026-08-10* |
| [performance-notes.md](performance-notes.md) | Chunk size as a recall setting, a chunker bug, throughput scaling with document length, and a throttled-GPU diagnosis | `privaparse bench --matrix --repeats 3` | 2026-08-10* |
| [gateway-latency.md](gateway-latency.md) | What the gateway adds to a request: cold vs. warm latency on 52 KB / 209 KB coding-agent payloads | `python eval/gateway_latency.py --sizes 50 200 --repeats 5` | 2026-08-12 |
| [gateway-fidelity.md](gateway-fidelity.md) | Whether a small local model echoes a placeholder back byte-for-byte | `python eval/placeholder_fidelity.py`, `python eval/e2e_real.py` | 2026-08-12 |
| [gateway-fallbacks.md](gateway-fallbacks.md) | Restoration rate across `PRIVAPARSE_GATEWAY_FUZZY` / `PRIVAPARSE_GATEWAY_HINT`, six cases, three rounds | `python eval/restore_matrix.py --label "fuzzy=off hint=off" --model qwen --verbose` | 2026-08-13* |
| [codex-cli.md](codex-cli.md) | A real Codex CLI 0.147.0 session through the Responses adapter — not a script, a live client | manual: `codex exec --skip-git-repo-check "…"` against a running gateway | 2026-08-12 |

\* Not stated inside the report itself; this is the date the file was added
to the repository, taken from git history. Where a report gives its own
"Measured" or "Date" field, that value is used instead.

## Headline figures

**PERSON detection**, from [detection-quality.md](detection-quality.md):
support 99, precision 0.969, recall 0.960, F1 0.964 (partial-match columns
— the ones the verdict uses), measured 2026-08-14 against the full
124-document gold set. This is the same run behind the top-level README's
own table now — the two used to disagree (this file lagged the README by
one gold-set growth spurt), and Task 5b closed that gap by regenerating both
together rather than leaving the older one to quietly go stale. The number
this superseded: support 76, precision 0.973, recall 0.961, F1 0.967,
measured 2026-08-10 on the 91-document set that predates Batches D and E
(see "About the gold set" below). Recall moved down, not up — expected,
since every document those two batches added was a positive, which is
exactly where a new miss hides; it still clears the pre-registered floor by
a wide margin (0.960 against a 0.90 bar).

**Throughput**, from [throughput.md](throughput.md): the fastest
configuration that still clears the PERSON quality floor is
`cuda/fp16/compile/b1` — p50 5.8 ms, 163.1 docs/s, PERSON recall 0.983,
measured on an RTX 3060 12 GB. The default `b8` batch size is within
measurement noise of it (p50 6.1 ms, 161.0 docs/s); batching only starts to
matter once chunks from several documents are pooled into one call, which
is not implemented yet.

## About the gold set

[detection-quality.md](detection-quality.md) and [labels.md](labels.md) now
both measure against the same **124 documents, 33 of them containing no PII
at all** (verified directly against `eval/gold/de_gold.jsonl`, which
currently holds ids `de-001` through `de-124`). That was not always so for
the first of those two: detection-quality.md and the top-level README's
headline table both described a 91-document run until 2026-08-14 — Task 13
added Batches D and E (`de-092`–`de-124`, 33 more documents) after that run,
every one of them containing PII, so the total grew from 91 to 124 while the
no-PII count held at 33. Task 5b re-ran the detection-quality measurement
against the grown set and updated both documents that quoted it; the
91-document numbers are kept alongside the new ones in each for comparison,
not deleted. labels.md already measured against the full 124 before
that — its per-label sweep predates this re-run and needed no update.
[throughput.md](throughput.md) and [performance-notes.md](performance-notes.md)
have **not** been regenerated and still describe an earlier, smaller
configuration — see the caveat on each in the top-level README before
treating a number from either as current.

Why the no-PII count matters at all: a gold set built only from documents
that contain PII can never produce a false positive, because there is no
negative case in it for a detector to get wrong. 33 of the 124 documents
here have nothing to redact, which is what lets this project's false-positive
counts mean anything.
