# Benchmark report

> Measured on a reference box (RTX 3060 12 GB, Ubuntu 24.04, 4 CPU cores), not on
> the development laptop, whose GPU is held at 210 MHz by firmware and produces
> numbers about its power policy rather than about this code. Same torch
> (2.13.0+cu130) and same model on both. See
> [performance-notes.md](performance-notes.md) section 4.
>
> Note the CPU row: four cores here against sixteen on the laptop, so that row is
> not comparable across machines. The GPU rows are.

Latency and quality are reported together on purpose. A configuration that is faster because it detects fewer names is not an optimisation.

Quality floor (fixed in advance): PERSON partial recall >= 0.9, precision >= 0.85.

Corpus: 38 documents, median 198 bytes (range 106–320). These are short letters and file notes, so per-document overhead dominates and **ms/KB reads much worse than it would on real multi-page documents** — compare p50 across configurations, not ms/KB across corpora.

| Config | Device | dtype | compile | batch | p50 ms | p95 ms | ms/KB | docs/s | VRAM MB | PERSON P | PERSON R | Status |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| cuda/fp16/c1/b8 | cuda:0 | fp16 | on | 8 | 6.1 | 7.7 | 32.4 | 161.0 | 621 | 0.967 | 0.983 | ok |
| cuda/fp16/c0/b8 | cuda:0 | fp16 | off | 8 | 9.3 | 10.0 | 48.7 | 107.3 | 625 | 0.967 | 0.983 | ok |
| cuda/fp32/c0/b8 | cuda:0 | fp32 | off | 8 | 13.3 | 15.2 | 71.6 | 73.0 | 1207 | 0.967 | 0.983 | ok |
| cuda/fp16/c1/b1 | cuda:0 | fp16 | on | 1 | 5.8 | 7.3 | 32.0 | 163.1 | 621 | 0.967 | 0.983 | ok |
| cuda/fp16/c1/b16 | cuda:0 | fp16 | on | 16 | 5.8 | 7.4 | 32.0 | 163.0 | 621 | 0.967 | 0.983 | ok |
| cpu/fp32/c0/b8 | cpu | fp32 | off | 8 | 359.3 | 414.7 | 1916.7 | 2.7 | – | 0.967 | 0.983 | ok |

## Recommendation

Fastest configuration that still meets the quality floor: **cuda/fp16/c1/b1** — p50 5.8 ms, 163.1 docs/s, PERSON recall 0.983.

In practice use the default `b8`: the three compiled rows are within measurement
noise of each other, because a document of this length is one or two chunks and
there is nothing to batch. Batch size starts to matter once chunks from several
documents are pooled into one call, which `detect()` does not do yet.

## Sustained throughput

Same box, `chunk_chars=512`, one document of increasing length:

| Input | chunks | ms | ms per chunk | KB/s |
| --- | ---: | ---: | ---: | ---: |
| 0.2 KB | 1 | 10 | 9.7 | 20.4 |
| 4 KB | 12 | 78 | 6.5 | 50.6 |
| 7 KB | 22 | 140 | 6.3 | 52.7 |
| 29 KB | 88 | 552 | 6.3 | 53.3 |
| 58 KB | 176 | 1108 | 6.3 | 53.1 |

Flat at ~53 KB/s across a 300-fold range. Cost is chunks × constant; document
length enters only through the chunk count.
