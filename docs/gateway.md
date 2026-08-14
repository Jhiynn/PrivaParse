# Gateway

One command, and a client that speaks Chat Completions is going through
PrivaParse:

```bash
privaparse run -- aider
```

`run` starts a gateway if none is listening, sets `OPENAI_BASE_URL` in the
child's environment, and exits with the child's own exit code. Your
`OPENAI_API_KEY` is passed through untouched — the gateway forwards it to the
provider and stores no credential of its own.

Or run the gateway yourself and point things at it — `run` reuses a gateway
that's already listening rather than starting a second one, so this still
works cross-platform:

```bash
privaparse serve
```

```bash
privaparse run -- aider
```

Both need the `[model]` extra installed — with only `[gateway]`, the server
starts fine but returns a 500 on the first request that reaches detection.
Run `privaparse --detector regex serve` instead to run without it, at the
cost of person detection.

## Which clients this actually works with

The gateway speaks **two wire protocols**: OpenAI Chat Completions and the
OpenAI Responses API. It serves `/v1/chat/completions`, `/v1/responses` (both
streaming and non-streaming, tool calls included) and `/v1/models`.

| Client | Works | Why |
| --- | --- | --- |
| `curl`, OpenAI Python/Node SDK | **yes**, exercised in the suite and against a live vLLM | Chat Completions |
| **Codex CLI** | **yes** — a real `codex exec` turn completed through the gateway | Responses API — see below |
| Aider, Continue, Cline, LangChain, LlamaIndex | expected — **not tested here** | Chat Completions against a `base_url` |
| Open WebUI, LibreChat | protocol yes — **but read the warning below first** | server-side clients put restored PII on that host |
| **Claude Code** | **no** | speaks the Anthropic Messages API and reads `ANTHROPIC_BASE_URL`, not `OPENAI_BASE_URL` |

## Codex CLI

Codex speaks only the Responses API — `wire_api = "chat"` was removed in
February 2026 — which is why the second adapter exists. Point it at the
gateway in `~/.codex/config.toml`:

```toml
model_provider = "privaparse"

[model_providers.privaparse]
name = "PrivaParse"
base_url = "http://127.0.0.1:8787/v1"
env_key = "OPENAI_API_KEY"
wire_api = "responses"
```

Verified against Codex CLI 0.147.0: a full turn completed, the email in the
prompt was pseudonymised before it reached the model, and the instruction
block Codex resends every request hit an 0.85 cache rate across two turns at
32 ms of PrivaParse overhead. Full account in
[benchmarks/codex-cli.md](benchmarks/codex-cli.md).

Two things that run turned up:

- Codex sends `client_metadata`, which appears in no published schema. It is
  now ignored — its contents are session identifiers, nothing typed by a
  person — but the first run refused the request with a 502 naming it, which
  is the fail-closed rule doing its job. Anything else unrecognised behaves
  the same way, and the 502 body tells you which field.
- **`function_call` and `function_call_output` are implemented and unit-tested
  but have not been seen from a real Codex** — the small local model never
  invoked a tool. Expect those paths to get their first real exercise on your
  first turn that touches a file.

Pointing Codex at a *local* model needs `--max-model-len 32768` or larger:
Codex sends ~20 KB of instructions and ten tool definitions before you type
anything, which does not fit in 8k.

## What it does to a request

Every text-bearing field is extracted, pseudonymised under **one mapping per
request**, and written back before anything is forwarded. One mapping matters:
the answer mixes placeholders from every message, and `reverse` resolves
against exactly one session.

**The request path fails closed.** A field the gateway has no rule for stops
the request with a 502 and nothing reaches the provider — a gateway that
forwards what it does not understand leaks the first time a client adopts a
new API feature, and does it silently. **The response path never aborts**: if
restoration fails you get the answer with placeholders standing, because at
that point the answer already exists and has been paid for.

Streamed answers are restored through a hold-back buffer, because
`[[PERSON_A1]]` arrives split across events. Streamed tool calls are collected
and emitted once complete, with their arguments parsed and re-serialised
rather than string-substituted — a restored name containing a quote would
otherwise produce arguments the client cannot parse.

Detection is cached per text block, keyed by the catalogue as well as the
text. A chat client resends its whole history every turn, so most blocks of
any request but the first were detected already.

## What it costs

Measured on an RTX 3060 against a coding-agent payload, with the provider
stubbed out — the provider's own latency is not PrivaParse's to report and
dwarfs these numbers anyway:

| Payload | First turn (cache empty) | Later turns (median) |
| ---: | ---: | ---: |
| 52 KB | 3.40 s | 0.27 s |
| 209 KB | 12.32 s | 1.07 s |

65 ms per KB cold, 5.2 ms per KB warm; the detection cache takes about 92 % off
a repeated turn. Both scale linearly with payload size, so they extrapolate.

The warm second is not detection — that is cached — it is span resolution and
the vault writes behind it, which every request does because a mapping is what
scopes an answer to one session. Full method, environment and caveats in
[benchmarks/gateway-latency.md](benchmarks/gateway-latency.md); the most
important caveat is that the measurement ran on four cores, so the warm figure
should improve on a workstation.

## Restoration puts real PII into the client

This is the thing to understand before deploying it anywhere but your own
machine. The gateway's whole job is to hand back **unredacted** answers. The
provider sees placeholders; the client sees real names.

So pointing a **server-side** client at this — Open WebUI, a shared LibreChat,
anything running on a host other than yours — places restored answers, with
real PII in them, on that host and in front of whoever else uses it. The tool
protects the hop to the model provider. It does nothing about the hop to the
client, because there is no such hop when the client is you.

For the same reason `serve` refuses any bind address that is not loopback. The
vault behind the gateway stores plaintext values and has no per-user access
control — it was built for one person on one machine — so a port the network
can reach is a vault the network can read back, whether or not the reader sent
the request that filled it. Reach it from another machine over an SSH tunnel,
not by binding a wider address.

## Known gaps

**Restoration needs the model to hand the placeholder back unchanged, and a
small model does not.** `reverse` matches exactly, so an answer containing
`[EMAIL_A1]` — one bracket pair short — restores nothing. Measured against
Qwen2.5-1.5B through vLLM: **0 of 6 placeholders came back byte-exact** in free
prose, mangled four different ways (bracket dropped, prose-ified to "Person
A1", omitted, quotes injected). Streamed *tool calls* were perfect across three
runs, because a schema field gets transcribed rather than composed.

Nothing leaks — the provider only ever saw the placeholder, and the user sees a
placeholder instead of their data, which is the safe direction. But it is
silent and it is total, and it hits hardest in exactly the configuration
`PRIVAPARSE_GATEWAY_UPSTREAM` exists to enable. Large models are far better at
verbatim reproduction; that has not been measured here. Full account in
[benchmarks/gateway-fidelity.md](benchmarks/gateway-fidelity.md),
reproduce with `eval/placeholder_fidelity.py` against your own model before
trusting a deployment.

**`PRIVAPARSE_GATEWAY_FUZZY=1` is the answer to it**, and on that model it took
restoration from 1 case in 6 to 6 in 6. It also accepts a placeholder the model
rewrote, matched only against the ones the current request issued — so it
widens how a placeholder may be spelled, never which mapping may resolve one.

`PRIVAPARSE_GATEWAY_HINT=1` asks the model, in a prepended system message, to
reproduce the tokens verbatim. **Measured, it makes things worse when fuzzy is
on** — the model stops mangling the placeholder and starts avoiding it, writing
"the specified email address" instead, which loses the value before restoration
can run. Kept as a switch because that may be an artefact of a small model.
Numbers and the raw outputs in
[benchmarks/gateway-fallbacks.md](benchmarks/gateway-fallbacks.md).

**`TAX_ID` recall is 0.000 on the gold set, and the gateway inherits that
exactly.** Four German Steuer-IDs in the gold set are detected as PHONE
instead — the bare form is matched by the phone backstop, the grouped form is
labelled `phone_number` by the model, and nothing produces a competing exact
span. They are still pseudonymised, so nothing leaves in the clear; they are
pseudonymised as the *wrong type*, which means a client asking about tax IDs
gets a placeholder that reads as a phone number. The full account, and why the
fix is deferred rather than missed, is under
[One defect, two numbers](benchmarks/detection-quality.md#one-defect-two-numbers).
Anyone reading only this section should still meet it.

**Tool declarations are forwarded unscanned.** A `tools` block is the client's
own schema — function names, descriptions, a parameter shape — and
pseudonymising it would degrade the model's choice of tool while protecting
nobody. A client that writes a person into a tool *description* sends that
person to the provider.

**A streaming request cannot report an upstream error status.** The relay
starts as soon as the provider responds, so a provider-side error arrives in
the stream body rather than as an HTTP status.

**The deprecated `functions` / `function_call` fields are refused**, not
ignored. Same shape as `tools` and the same argument, but nothing is tested
against them.

Running the gateway in a container: see [Docker](install.md#docker).
