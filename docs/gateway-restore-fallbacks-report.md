# Two ways to survive a model that will not echo a placeholder

[docs/gateway-model-fidelity-report.md](gateway-model-fidelity-report.md)
established the problem: restoration is an exact string match, and a small
model returns `[EMAIL_A1]` where `[[EMAIL_A1]]` went out. This measures the two
opt-in answers to it.

| Switch | What it does |
| --- | --- |
| `PRIVAPARSE_GATEWAY_FUZZY` | Also accept the ways a model rewrites a placeholder, matched only against the ones this mapping issued |
| `PRIVAPARSE_GATEWAY_HINT` | Prepend a system message asking the model to reproduce `[[TYPE_A1]]` verbatim |

Both default to off.

## Setup

vLLM 0.27.1 serving Qwen2.5-1.5B-Instruct on an RTX 3060, gateway with
`PRIVAPARSE_DETECTOR=regex`, `temperature=0`. Six cases: echo an address, echo
a phone number, answer a question about the address, use it in a sentence,
emit JSON, and the echo again over SSE. `eval/restore_matrix.py`, one gateway
restart per configuration, three full rounds.

**The three rounds were byte-identical**, so nothing below is sampling noise.

## Results

| Configuration | Restored | echo addr | echo phone | answer Q | in a sentence | JSON | streamed |
| --- | ---: | :-: | :-: | :-: | :-: | :-: | :-: |
| neither (default) | **1 / 6** | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ |
| fuzzy only | **6 / 6** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| hint only | **4 / 6** | ✓ | ✓ | ✗ | ✗ | ✓ | ✓ |
| both | **5 / 6** | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ |

## The hint makes things worse when fuzzy is on

Adding the hint to fuzzy costs a case rather than winning one, which was not
the expected direction. The cause is visible in the raw model output for
"answer a question":

| | What the model produced, before restoration |
| --- | --- |
| without the hint | `Sie sollten die Rückfrage an [EMAIL_A1] schreiben.` |
| with the hint | `Sie sollten die Rückfrage an den angegebenen E-Mail-Adresse senden.` |

Without the instruction the model uses the placeholder and merely mistypes it —
which the tolerant matcher repairs. **With the instruction it avoids the token
altogether**, paraphrasing to "the specified email address". Told these tokens
are special and must be handled exactly, a small model plays it safe by not
handling them at all.

That failure is worse than the one the hint was meant to fix. A mangled
placeholder still carries the information and can be repaired; an answer that
paraphrases the value away has lost it before restoration is even reached.

## What to run

**On a model of this class: `PRIVAPARSE_GATEWAY_FUZZY=1`, hint off.** It took
restoration from 1 in 6 to 6 in 6 and is the only configuration that scored
perfectly.

The hint is still worth having as a switch — it is the only one of the two that
can help when the model drops the placeholder *without* mangling it, and its
avoidance behaviour is plausibly an artefact of a 1.5B model rather than a law.
On a frontier model the compliance is likely better and the avoidance weaker.
Neither of those is measured, which is the honest state of it.

## Caveats

**One model, one size.** Everything here is Qwen2.5-1.5B. The ranking may
invert on a larger model, and the hint is exactly the kind of thing that gets
better with capability.

**Fuzzy widens spelling, never scope.** The tolerant pattern is built per
entry of one mapping's restore table, so a placeholder this session never
issued matches nothing however it is written — pinned by
`test_a_mangled_placeholder_from_another_session_is_not_restored`. What it does
cost is strictness: with it on, `Person A1` in an answer will be treated as a
placeholder if this mapping issued `[[PERSON_A1]]`. That is the trade being
opted into.

**Streaming needs the wider hold-back.** A mangled placeholder is only
repairable if it arrives whole, and the strict hold-back only ever protects
`[[`. Turning on fuzzy also widens the buffer to single brackets; a placeholder
written with no brackets at all still cannot be caught mid-stream.

## Reproducing

```bash
python eval/restore_matrix.py --label "fuzzy=off hint=off" --model qwen --verbose
```
