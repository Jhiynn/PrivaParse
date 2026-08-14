# Gateway latency

What PrivaParse adds to a request, measured against a coding-agent payload.

The provider's own latency is not in these numbers and is not the gateway's to
report — it dwarfs everything here and would bury the one figure an operator
can act on. The provider is a stub; nothing left the machine and no API key was
involved.

## Environment

| | |
| --- | --- |
| GPU | NVIDIA GeForce RTX 3060, 12 GB |
| CPU | 4 cores (isolated sandbox — **not** comparable to a workstation) |
| torch | 2.13.0, CUDA build, fp16, `compile=on` |
| Model | `fastino/gliner2-privacy-filter-PII-multi` (local snapshot, `PRIVAPARSE_OFFLINE=1`) |
| Catalogue | 21 enabled types, 31 labels |
| Chunk size | 512 characters (the default) |
| Measured | 2026-08-12, `eval/gateway_latency.py --sizes 50 200 --repeats 5` |

Same GPU as [docs/benchmarks/throughput.md](throughput.md) and the README's throughput
pair, so those numbers and these belong to the same machine. The core count
does not: four cores is a quarter of a workstation, and the parts of this that
are not on the GPU pay for that.

## Method

Two sizes: 50 KB is a modest agent working set (a system prompt plus a few
files), 200 KB a large one. The payload is two parts source code to one part
German prose, because real agent traffic is mostly source — code is masked
before detection, so a payload of pure prose would overstate the cost and one
of pure code would understate it.

Two conditions per size:

- **Cold** — the detection cache is empty. This is the first turn of a
  conversation. The model is already loaded and already compiled: the script
  warms up at full payload size first, because `torch.compile` fires on the
  first batch of a given shape and a short warmup string leaves the entire
  compilation inside the first measured request. Before that fix the 50 KB cold
  figure came out at 31.6 s against 209 KB's 12.4 s — a smaller payload timing
  three times slower, which is what a contaminated measurement looks like.
- **Warm** — the same request again, five times. Every text block is now in the
  detection cache. This is every turn after the first, and it is the condition a
  long conversation spends nearly all of its time in.

## Results

| Payload | Cold (first turn) | Warm median | Warm range | Unique entities | Occurrences |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 52.2 KB | 3.40 s | 0.27 s | 0.27–0.27 s | 6 | 354 |
| 209.2 KB | 12.32 s | 1.07 s | 1.07–1.08 s | 6 | 1,422 |

Per kilobyte: **65 ms cold, 5.2 ms warm**. The cache takes about 92 % off a
repeated turn.

Both conditions scale roughly linearly with payload — 4× the bytes costs 3.6×
cold and 4.0× warm — which is what a chunked detector and a per-span resolver
should do, and is the useful property here: the numbers extrapolate.

## What the warm number is actually paying for

Detection is cached; resolution is not, by design. A cached block still gets
its own mapping, because `reverse` scopes an answer to exactly one session and
reusing a mapping would let one request's answer resolve against another
request's session. So the warm second is not detection — it is 1,422 span
resolutions and the vault writes behind them.

That also sets where the next optimisation is, if one is wanted: the warm path
is dominated by occurrence count, not by unique entities.

## Caveats

**The payload repeats a unit.** Six unique entities at both sizes, with
occurrences scaling instead. Real traffic has more unique entities and fewer
repeats of each, which would shift work from resolution toward the vault's
entity lookups. The cold figure is the more transferable of the two.

**Four cores.** Chunking, masking, span merging and the SQLite writes are all
CPU-side. On a workstation the warm number in particular should improve; do not
read these as a laptop's numbers.

**First turn only.** The plan called for first-turn latency and that is what
this measures. A full conversation profile — twenty turns with a growing
history, where each turn adds one uncached block to a mostly-cached request —
is not measured here.
