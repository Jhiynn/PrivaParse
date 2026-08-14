# Quickstart

```bash
printf 'Mein Name ist Max Mustermann, erreichbar unter max@test.de.\n' > brief.md
PRIVAPARSE_DETECTOR=regex privaparse demo brief.md
```

`demo` runs the whole round trip and prints every stage — original, detections,
pseudonymised text, a mock LLM answer, and the restored result. It is the
fastest way to see whether the thing works on your documents. Person
detection needs the `[model]` extra; without it, `PRIVAPARSE_DETECTOR=regex`
limits detection to email and phone.

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
