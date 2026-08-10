# Round-trip against a real cloud LLM

Phase 1 proved the pipeline against a mock: text in, placeholders out, canned
answer, originals restored. The mock is cooperative — it echoes placeholders back
verbatim because it was written to. A real model has no such obligation.

This design replaces the mock with OpenAI for the purpose of **measuring** what
actually comes back.

## The question

Not "do all placeholders survive" — an LLM asked to summarise may legitimately
mention none of them, and that is not a failure.

The question is narrower and answerable:

> When the model does emit a placeholder, is it byte-identical to what we sent,
> and does `reverse()` therefore resolve it?

A placeholder that comes back as `**[[PERSON_A1]]**` or `[PERSON_A1]` is worse
than one that is omitted. Omission loses information; deformation leaves a name
masked in a document the user believes has been restored, and the user has no
signal that it happened.

## Metric

Every placeholder-shaped token in the model's answer falls into one class:

| Class | Example | Meaning |
| --- | --- | --- |
| `exact` | `[[PERSON_A1]]` | resolves; the good case |
| `deformed` | `[PERSON_A1]`, `**[[PERSON_A1]]**`, `[[ PERSON_A1 ]]`, `PERSON_A1` | recognisably ours, `reverse()` misses it |
| `invented` | `[[PERSON_Q9]]` never issued | hallucination |
| `foreign` | issued to a different session | must never occur; counted anyway |

Fidelity = `exact / (exact + deformed)`. Invented and foreign are reported
separately — they measure something else and must not be averaged in.

Deformed placeholders are **counted, not repaired.** Loosening `reverse()` to
accept variants would enlarge the surface for guessed placeholders, and doing it
before the measurement exists would be guessing at which variants matter. Measure
first.

## Components

A `LlmClient` protocol, mirroring the existing `Detector` protocol so that every
test runs against a fake and nothing touches the network by accident.

```
privaparse/llm/
├── base.py       LlmClient protocol, LlmResponse(text, model, tokens_in, tokens_out)
├── mock.py       today's app/mock_llm.py, moved here for symmetry
├── openai.py     OpenAIClient — httpx directly, no SDK (httpx is already a dependency)
├── fidelity.py   the classifier above
└── guard.py      the residual-value check below
```

`app/mock_llm.py` moves to `privaparse/llm/mock.py`. Two import lines change
(`app/main.py`, `tests/test_mvp.py`). The move buys symmetry between the two
clients; without it the mock sits in `app/` while its sibling sits in `llm/`.

## Command

```bash
privaparse roundtrip brief.md --prompt "Formuliere eine Antwort" --model gpt-4o-mini
```

`--model` is repeatable, so one run compares several models against each other —
the same shape as `privaparse eval`. Pointed at the gold set it produces a rate
rather than an anecdote.

The command owns the whole cycle; it does not take an already-pseudonymised file:

1. Pseudonymise the input, creating a mapping like any other run. The vault is
   written to exactly as `pseudonymize` would write to it — a round trip is not
   a dry run.
2. Guard the outgoing text (below), then send it with the prompt.
3. Classify every placeholder-shaped token in the answer.
4. Reverse the answer against that mapping.
5. Report.

Files written, alongside the report: `<name>.pseudo.md` (what was sent),
`<name>.answer.md` (what came back), `<name>.restored.md` (after reversal). Being
able to read all three side by side is most of the diagnostic value; `--no-files`
suppresses them for gold-set runs, where 38 documents times three files is noise.

Output: the four counts, the fidelity rate, a dump of every deformation observed
(verbatim, so the variants are visible), token usage and estimated cost. Written
to `docs/roundtrip-report.md` like the other harnesses.

## Egress is explicit

PrivaParse's promise is that the document does not leave the machine. Adding a
network path needs that promise to stay legible.

- **Only `roundtrip` sends.** No configuration value causes `demo`,
  `pseudonymize`, `detect`, `eval` or `bench` to open a socket. There is no
  `PRIVAPARSE_LLM=openai` switch that retro-fits network access onto an existing
  command.
- **No key, no call.** A missing `OPENAI_API_KEY` aborts with a clear message.
  It never silently falls back to the mock — a silent fallback would make a
  failed integration look like a passing test.
- **The key lives in the environment only.** Never in `.env.example`, never in
  the vault, never logged. The redacting log filter already covers accidental
  interpolation.
- **The destination is named before sending**, and token usage plus estimated
  cost are printed after.

## The residual-value guard

Before any text goes out, check it against the restore table for this mapping:
if an original value is still present, pseudonymisation did not do its job, or —
far more likely — the caller passed the wrong file, the original instead of the
`.pseudo` one. That is the realistic mistake, and it is the one place in this
project where a bug ships data off the machine instead of merely spoiling a file.

The check cannot be uniform, because not every hit is a leak. A document may
contain `Winter` as a surname *and* as a season; the season survives
pseudonymisation correctly. So the guard splits by how unambiguous the value is:

| Value | Can it be ordinary prose? | Action |
| --- | --- | --- |
| EMAIL (contains `@`) | no | **refuse to send** |
| PHONE (digit sequence) | no | **refuse to send** |
| PERSON, two or more tokens | practically never | **refuse to send** |
| PERSON, single token (`Anna`, `Winter`) | yes | warn, send anyway |

`--allow-residual` overrides the refusal for the rare legitimate case. The
warning is never suppressed.

## Testing

- Everything runs against a `FakeLlmClient` returning canned answers, including
  one answer per deformation class. No network in the default suite.
- `fidelity.py` gets its own tests: each class, and the boundary cases —
  a placeholder inside a code fence, one that spans a line break, one that
  differs only in case.
- The guard gets tests for each row of the table above.
- The single test that actually calls OpenAI is marked `@pytest.mark.network`
  and excluded by default, alongside the existing `model` marker.

## Cost

Default `gpt-4o-mini`. A gold-set run is 38 calls; the count is printed before
the first one. `--max-tokens` caps the response. No confirmation prompt — the
command itself is the confirmation.

## Out of scope

Retries, rate-limit backoff, streaming, async, other providers. The protocol
makes another provider a new file, but nothing here needs one yet. This is a
measuring instrument, not an LLM client library — if the fidelity rate turns out
good, promoting it to `engine.ask()` is a separate, later decision.
