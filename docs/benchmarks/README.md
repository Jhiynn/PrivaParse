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
| [detection-quality.md](detection-quality.md) | PERSON/EMAIL/PHONE precision, recall and F1 for the hybrid detector against the German gold set | `privaparse eval` | 2026-08-10* |
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
support 60, precision 0.967, recall 0.983, F1 0.975 (partial-match columns
— the ones the verdict uses). Read this number for what it is, not for what
the top-level README currently quotes: the README's own table states PERSON
at P 0.973 / R 0.961 / F1 0.967 on 76 supporting entities, scored at 35
labels — a newer, larger run than the one in this file. The README says so
plainly (`docs/benchmarks/detection-quality.md has not yet been regenerated
for this configuration`), and this index repeats the caveat because a reader who
follows the link from here would otherwise see the older number with
nothing to explain the gap. Rerunning `privaparse eval` today will not
reproduce either number: the gold set has grown since both measurements
(see below).

**Throughput**, from [throughput.md](throughput.md): the fastest
configuration that still clears the PERSON quality floor is
`cuda/fp16/compile/b1` — p50 5.8 ms, 163.1 docs/s, PERSON recall 0.983,
measured on an RTX 3060 12 GB. The default `b8` batch size is within
measurement noise of it (p50 6.1 ms, 161.0 docs/s); batching only starts to
matter once chunks from several documents are pooled into one call, which
is not implemented yet.

## About the gold set

[labels.md](labels.md) measures against **124 documents, 33 of them
containing no PII at all** (verified directly against
`eval/gold/de_gold.jsonl`, which currently holds ids `de-001` through
`de-124`). That is larger than the 91-document set the top-level README
describes for its current headline table — Task 13 added Batches D and E
(`de-092`–`de-124`, 33 more documents) after that run, and every one of
them contains PII, so the total grew from 91 to 124 while the no-PII count
held at 33.

Why the no-PII count matters at all: a gold set built only from documents
that contain PII can never produce a false positive, because there is no
negative case in it for a detector to get wrong. 33 of the 124 documents
here have nothing to redact, which is what lets this project's false-positive
counts mean anything.
