# Performance notes

Two machines, same code, same torch (2.13.0+cu130), same model
(`fastino/gliner2-privacy-filter-PII-multi`, encoder `microsoft/mdeberta-v3-base`):

| | Dev laptop | Reference box |
| --- | --- | --- |
| GPU | RTX 4070 Laptop (8 GB) | RTX 3060 (12 GB) |
| Clock under load | **210 MHz of 3105** | 1837 MHz of 2100 |
| Power under load | **9 W**, ceiling pinned at 15 W | 44 W of 170 W |
| OS | Windows 11, hybrid graphics | Ubuntu 24.04 |
| `torch.compile` | unavailable (no Triton on Windows) | works |

The laptop's GPU is held in its idle power state by firmware (section 4). Every
GPU timing taken there is a measurement of that policy, not of this code, and
the reference box exists to say by how much: **14x on the same configuration**,
on a card that is nominally the weaker of the two.

Chasing a throughput number also turned up a **recall** defect, which is the
finding worth keeping (section 1).

## 1. Chunk size is a recall setting, not a speed setting

This section does not depend on GPU timing and stands.

Same 7.2 KB German document (the gold set concatenated, entity offsets shifted),
scored through the full pipeline — protection, merge, coreference sweep —
against its own annotations:

| `chunk_chars` | chunks | PERSON precision | PERSON recall |
| ---: | ---: | ---: | ---: |
| 384 | 33 | 0.983 | **0.967** |
| **512** | 22 | **1.000** | **0.950** |
| 768 | 14 | 1.000 | 0.950 |
| 1024 | 10 | 1.000 | 0.950 |
| 1500 *(old default)* | 6 | 1.000 | **0.900** |
| 2048 | 4 | 1.000 | 0.900 |

EMAIL and PHONE recall stay at 1.000 throughout — they come from rules, so chunk
size cannot touch them. Only the model-driven type moves.

**Why.** GLiNER scores candidate spans against the whole chunk. A longer chunk
means more competing candidates, scores get diluted, and names fall under the
threshold. Shorter chunks focus the comparison.

**The default is now 512.** Same precision as 1500, five points more recall. 384
buys another 1.7 points but starts costing precision, so it is not the default —
set it deliberately if recall outweighs everything else.

This does not change `docs/benchmarks/detection-quality.md`: every gold document is shorter than
one chunk, so chunking never engages there. The effect is invisible on short
inputs and shows up only on documents long enough to split — which is to say, on
real ones.

## 2. A chunker bug, found by the same sweep

With a fixed 200-character overlap and a small window, `chunk_text` could end a
chunk halfway into the window (`_split_point` is allowed to back off to the
midpoint), at which point `end - overlap` landed *behind* where the chunk started
and the loop crawled forward one character at a time.

Measured at `chunk_chars=256` on a 7.3 KB document: **2374 chunks and 203
seconds**, against roughly 30 chunks and 5 seconds expected.

The old default of 1500 was far outside the danger zone, so nothing shipped was
affected — but the knob was a trap for exactly the tuning section 1 now
recommends. Fixed by capping `overlap` at a quarter of the window and requiring
each step to advance. Guarded by
`test_chunk_count_stays_proportional_to_document_size` and neighbours.

## 3. Document length costs nothing beyond chunk count

An earlier version of this file claimed "the GPU inverts past 4 KB per document"
and called it measured. It was wrong twice over, and both errors are worth
recording because both are easy to repeat.

**First confound: chunk length, not document length.** That run varied document
size at a fixed `chunk_chars=1500`. A document below the window is a single chunk
*of its own length*, so 0.2 KB meant a 200-character chunk while 4 KB meant
1500-character chunks. It compared chunk lengths and reported document lengths.

**Second confound: the card was throttled** (section 4), and sizes were measured
in ascending order, so the card grew hotter and slower as the documents grew
larger. Correlation, no causation.

Holding chunk length fixed at 512 and varying only the chunk count, on the
reference box:

| Input | chunks | ms | ms per chunk | KB/s |
| --- | ---: | ---: | ---: | ---: |
| 0.2 KB | 1 | 10 | 9.7 | 20.4 |
| 1 KB | 3 | 21 | 7.1 | 46.5 |
| 4 KB | 12 | 78 | 6.5 | 50.6 |
| 7 KB | 22 | 140 | 6.3 | 52.7 |
| 15 KB | 44 | 277 | 6.3 | 53.0 |
| 29 KB | 88 | 552 | 6.3 | 53.3 |
| 58 KB | 176 | 1108 | 6.3 | 53.1 |

Cost per chunk converges to 6.3 ms and stays there across a 300-fold range of
document size. Throughput is flat at ~53 KB/s. **Total cost is exactly chunks ×
cost-per-chunk; document length enters only through the chunk count.** There is
no inversion and no degradation.

Cost per chunk does grow superlinearly with chunk *length* — consistent with
self-attention being quadratic in sequence length. An earlier note blamed the
model's `count_lstm` layer. Also wrong: `CountLSTM.forward` runs at most
`max_count=20` steps over one embedding per entity type (three, here), entirely
independent of chunk length.

## 3b. What the hardware is actually worth

Full matrix on the reference box, gold set, three passes, quality scored in the
same run:

| Config | p50 ms | docs/s | VRAM MB | PERSON P | PERSON R |
| --- | ---: | ---: | ---: | ---: | ---: |
| cuda / fp16 / compile / b1 | **5.8** | 163.1 | 621 | 0.967 | 0.983 |
| cuda / fp16 / compile / b16 | 5.8 | 163.0 | 621 | 0.967 | 0.983 |
| cuda / fp16 / compile / b8 | 6.1 | 161.0 | 621 | 0.967 | 0.983 |
| cuda / fp16 / eager / b8 | 9.3 | 107.3 | 625 | 0.967 | 0.983 |
| cuda / fp32 / eager / b8 | 13.3 | 73.0 | 1207 | 0.967 | 0.983 |
| cpu / fp32 / b8 (4 cores) | 359.3 | 2.7 | – | 0.967 | 0.983 |

Read across the rows and three things fall out:

**Quality does not move.** PERSON 0.967 / 0.983 in all six configurations, on two
different GPUs, two operating systems, and CPU. dtype, compilation and batch size
are speed knobs and nothing else — which is what the cross-device test in
`pytest -m model` asserts, now confirmed on real hardware.

**fp16 is free money**: 1.4x faster and half the VRAM at identical scores.
`torch.compile` adds another 1.5x, where the platform allows it.

**Batch size still changes nothing** (5.8 / 6.1 / 5.8 for b1 / b8 / b16). Not a
surprise and not a disappointment: `detect()` is called per document, and these
documents are one or two chunks each, so there is nothing to batch. This is the
measurement behind section 5.1 — the batching that would pay off is *across*
documents, and it does not exist yet.

## 4. The GPU was throttled for every measurement taken on the laptop

```
clocks.current.graphics : 210 MHz          clocks.max.graphics : 3105 MHz
power.draw              : 6–9 W            pstate              : P8
utilization.gpu         : 90–100 %         temperature         : 71–78 °C
SW Power Cap            : Active
SW Thermal Slowdown     : Active
```

The card sits in P8 — the *idle* power state — while fully loaded, at 6.8 % of
its maximum clock, drawing idle-level power. `nvidia-smi` reports the cap as
continuously active for ~59 hours, so this is a standing policy and not heat
generated by the benchmark. The machine was on AC power at 96 % charge
throughout.

The trap: **utilisation still reads 100 %**. Nothing in a timing table gives
this away. Every "the GPU is only 1.75x faster than the CPU" conclusion drawn
here was really "a 210 MHz GPU is 1.75x faster than the CPU".

`privaparse bench` now samples the clock at the end of each timed section and
puts a warning at the top of the report when it is below half of maximum. That
check did not exist while the numbers above were produced.

**Fixing it is a machine setting, not a code change.** On this laptop the likely
owner is Lenovo Vantage, which was running (`LenovoVantageService`,
`LenovoUtilityService`) and whose thermal-mode setting governs the discrete
GPU's power envelope; `nvidia-smi` cannot even read the power limit, which fits a
firmware-managed cap. Worth checking in order:

1. Lenovo Vantage → thermal / power mode → **Performance** rather than Quiet or
   Balanced.
2. Windows Settings → System → Power & battery → Power mode → **Best
   performance**.
3. NVIDIA Control Panel → Manage 3D settings → Power management mode → **Prefer
   maximum performance**.

Confirm with `nvidia-smi --query-gpu=pstate,clocks.current.graphics,power.draw
--format=csv` under load: P0 and four-digit MHz means it is fixed. `SW Thermal
Slowdown` active at 71 °C also suggests an aggressive thermal target or a
cooling path worth inspecting.

## 5. What this says about the ONNX question

**Do not bother.** 53 KB/s and 163 documents/second on a mid-range RTX 3060, at
6.3 ms per chunk that stays flat to 58 KB, is not a workload crying out for a
different inference backend. ONNX/INT8 would chase a fraction of an already
small number, and INT8 would put the recall floor at risk to do it. The question
that opened this file is answered: no.

Two things are worth doing before anyone reconsiders that:

1. **Batch across documents.** `detect()` takes one document; its chunks are
   batched, but 50 documents mean 50 separate model invocations with the card
   idle between them. Section 3b shows batch size making no difference precisely
   because there is nothing to batch within one short document. A
   `detect_many(texts)` pooling chunks across documents is where the remaining
   headroom is — a 512-character chunk uses a fraction of the card.
2. **Fix the laptop.** 14x on identical software, from a card that is nominally
   slower. Nothing in this repository will ever buy that much.

**Flash attention** (`PRIVAPARSE_FLASH_ATTENTION=true`, needs the `flashdeberta`
package) attacks the quadratic self-attention term and remains available, but at
6.3 ms per chunk there is little left for it to win.

---

## Reproducing this

The reference numbers came from a disposable GPU sandbox, not from special
hardware:

```bash
pip install -e ".[model,dev]"
```

```bash
python -m privaparse.evaluation.build_gold
```

```bash
privaparse bench --matrix --repeats 3
```

`privaparse doctor` first — if it reports a clock below half of maximum, or
`compile=off (triton …)`, the numbers will describe the machine rather than the
code. `bench` prints a warning at the top of its report when it detects this.
