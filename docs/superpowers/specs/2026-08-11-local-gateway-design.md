# Local OpenAI-Compatible Gateway

Design document. 2026-08-11. Second spec of two; the first was the entity
catalogue and its measurement loop, which this builds on.

## Problem

PrivaParse works and nobody uses it. It is a CLI: you pseudonymise a file, you
paste the result somewhere, you paste the answer back, you reverse it. Every
step is manual, and a privacy layer that requires four manual steps per prompt
is a privacy layer that gets skipped on the fifth prompt.

The tools that would benefit most — Cursor, Cline, Continue, Aider, Open WebUI,
n8n, any script using the `openai` SDK — all speak one protocol and all accept
a `base_url` override. That is the seam. A local process that speaks the same
protocol, pseudonymises on the way out and restores on the way back, turns four
manual steps into an environment variable.

## Goals

- An OpenAI-compatible endpoint on `127.0.0.1` that any such client can be
  pointed at with `OPENAI_BASE_URL`.
- `privaparse run -- <command>` — start-or-reuse the daemon, inject the
  variable into the child's environment, run the command, propagate its exit
  code. One word in front of an existing command.
- Restoration in streaming responses, including placeholders split across
  server-sent events.
- Detection cached per message block, so a coding agent resending a 200 KB
  context pays for the new paragraph rather than the whole conversation.
- The request path fails closed. Anything it cannot scan does not leave.

## Non-goals

- No authentication, no user management, no multi-tenancy, no audit log, no
  admin UI, no provider routing, no cost tracking. The gateway binds
  `127.0.0.1` and serves one person.
- No Anthropic Messages API in this spec. The extraction seam is built so it
  is a second adapter file, not a rewrite.
- No storage of provider credentials. The client's `Authorization` header is
  forwarded untouched.

## What the measurement changed about this design

Two findings from the catalogue work bear directly on the gateway, and both
push in the same direction.

**Throughput is 26 documents per second at the shipped configuration** — an
RTX 3060, fp16, 124 documents averaging under a kilobyte. A coding agent sends
50 to 500 KB per turn. Without caching that is seconds of added latency on
every keystroke-triggered completion, which is the difference between a tool
people use and a tool people uninstall. The delta cache is not an optimisation;
it is the feature.

**The resolution stage does most of the work.** Raw model output at the shipped
label count scores 0.438 precision; the same output through
`resolve_spans` — overlap resolution, exact-boundary trimming, per-type
thresholds, checksum vetoes — scores 0.753. The gateway must therefore call the
same engine path the CLI calls, never the detector directly. A gateway that
called `detector.detect()` to save a step would ship roughly four times the
false positives, and its author would not notice, because the output would
still look plausible.

## Architecture

One new package. The engine, catalogue and parser are untouched.

```
privaparse/gateway/
  server.py       Starlette app: /v1/chat/completions, /v1/models, /healthz
  adapter/
    openai.py     request JSON -> [(json_pointer, text)] and back
  extract.py      recursive walker plus a whitelist of text-bearing fields
  cache.py        content-hash -> detection spans, in-memory LRU
  stream.py       SSE restoration with a hold-back buffer
  upstream.py     httpx client, header passthrough, stream relay
privaparse/app/main.py
  serve           run the daemon
  run             daemon + environment injection + child process
```

**The seam is `extract.py`.** The adapter knows OpenAI's JSON shape and hands
the core a list of `(pointer, text)` pairs. The core knows no protocol. A second
adapter is a new file.

### Request path

```
client --POST /v1/chat/completions--> gateway
  1. extract    walk the JSON, whitelist of known text fields
  2. hash       per text node, content hash -> cache lookup
  3. detect     cache misses only, through engine.detect_many
  4. resolve    vault lookup, ONE mapping for the whole request
  5. writeback  text back at each pointer, tool-call arguments re-serialised
  6. forward    httpx, client's Authorization passed through
```

Walked: `messages[].content` in both its string and array forms,
`messages[].name`, `messages[].tool_calls[].function.arguments` (a JSON string —
parsed, then every string and number leaf walked), `tools[].function.description`
and its parameter descriptions, and `system`.

**One mapping per request, via `pseudonymize_batch`.** The whole conversation
history is re-pseudonymised on every request, so every placeholder in the
outgoing payload was issued by that request's mapping, and `reverse` resolves
the answer against exactly that session. A placeholder the model invents
resolves to nothing, which is the session-scoping guarantee working as designed.

### Cache

Only detection is cached, keyed by the content hash of a message block.
Resolution and the mapping entry run every time.

| Stage | Cached | Why |
| --- | --- | --- |
| Detection | yes | The expensive part. Deterministic for a given text and catalogue. |
| Vault resolve | no | SQLite lookups, cheap. |
| Mapping entry | no | Must record which placeholders *this* request was issued. |

That split is what keeps `reverse`'s session scoping exactly as sharp as it is
in the CLI while the cost disappears. The cache key must include a catalogue
fingerprint: a cached span set is only valid for the catalogue that produced it,
and the catalogue is a file an operator edits.

### Response path

The asymmetry matters and is deliberate. **The request path fails closed; the
response path never aborts.** A failure on the way out risks disclosure. A
failure on the way back means the user sees a placeholder — visible, harmless,
and cheaper than discarding an answer that was already paid for.

**Streaming.** A placeholder such as `[[PERSON_A1]]` arrives across several SSE
events. A restorer per stream holds back a tail:

```
delta arrives -> append to buffer
  find the last unclosed "[["
  everything before it is safe -> restore, emit as an SSE event
  hold from "[[" onward
  buffer grows past the longest possible placeholder -> it was not one, release
stream ends -> flush
```

**Tool calls cannot stream through.** `delta.tool_calls[].function.arguments`
arrives as JSON fragments, unparseable until complete. Buffer per index until
`finish_reason`, then parse, restore, emit as one piece. Nothing is lost: a
partial tool call is not executable anyway.

**`usage` is passed through untouched.** The token counts describe the
pseudonymised text, which is the text that was billed. Rewriting them would be
a lie.

### Fail-closed contract

The request path returns HTTP 502 rather than forwarding when it meets an
unknown JSON field containing a string, an image or other non-text modality, a
`tool_calls` argument string that will not parse, or a detector failure. The
body says which field and why.

This will break when a provider adds a field. That is the intended trade: a
gateway that forwards what it does not understand is a gateway that leaks the
first time a client adopts a new API feature, silently.

## Shipping

| Route | For | Contents |
| --- | --- | --- |
| `pip install privaparse[gateway]` | local | adds starlette, uvicorn, httpx |
| `ghcr.io/…/privaparse:slim` | servers, n8n | weights fetched on first start |
| `ghcr.io/…/privaparse:full` | air-gapped | weights baked, `PRIVAPARSE_OFFLINE=1` |

Activation is the part that decides whether this gets used:

```bash
privaparse run -- aider
```

Checks `127.0.0.1:8787/healthz`, starts the daemon if it is not there, sets
`OPENAI_BASE_URL` in the child environment, passes `OPENAI_API_KEY` through
unchanged, execs the command, propagates the exit code. No config file, no
certificate, no manual environment editing. For GUI clients such as Cursor,
`privaparse serve` plus the base URL in settings, once.

Observability: `privaparse gateway stats` — requests, cache hit rate, entities
per request, p50 latency. **Never content.** That is one line in the spec and
the property that distinguishes this from every hosted gateway.

## Risks

**Latency on first contact.** The cache is empty when a conversation starts, so
the first turn of a long context pays full price — seconds. Subsequent turns
pay for the delta. Whether that first turn is tolerable is unmeasured, and the
plan must measure it against a realistic coding-agent payload before the design
is trusted.

**The catalogue is an operator-editable file.** Editing it invalidates every
cached span set. The fingerprint in the cache key handles correctness; the cost
is a cold cache after every edit, which is right but should be visible in the
stats rather than mysterious.

**Restoration puts real PII into the client.** That is the point for a local
editor. For a client that is itself a server — Open WebUI on a shared host —
the restored answer lands there. This spec does not address it and the README
must say so plainly rather than let a reader assume the boundary is wider than
it is.

**A known detection defect ships with it.** `TAX_ID` has zero recall: the phone
backstop takes the bare rendering and the model labels the grouped one
`phone_number`. It is recorded in the gold set and the README. The gateway does
not make it worse and does not fix it.

## Out of scope, in order of likely value

Anthropic Messages API adapter, so Claude Code and the Agent SDK work — one
file against the `extract.py` seam. Then the LiteLLM guardrail and Open WebUI
filter, each roughly a hundred lines once the engine API is shaped by this work.
Then the browser extension, which is a different product and a different spec.
