# Codex CLI through the gateway

The Responses adapter met the real client. This is what happened.

## Setup

| | |
| --- | --- |
| Client | Codex CLI 0.147.0 (`npm i -g @openai/codex`) |
| Upstream | vLLM 0.27.1 serving Qwen2.5-1.5B-Instruct, RTX 3060 |
| Gateway | `PRIVAPARSE_DETECTOR=regex`, upstream pointed at vLLM |
| Auth | `OPENAI_API_KEY=dummy` — vLLM checks nothing and the gateway stores nothing |
| Date | 2026-08-12 |

No account and no real key were involved. Which item types a client emits is
a property of the client, not of who answers, so a local model finds the same
gaps a paid one would.

```toml
model = "qwen"
model_provider = "privaparse"
approval_policy = "never"
sandbox_mode = "read-only"

[model_providers.privaparse]
name = "PrivaParse"
base_url = "http://127.0.0.1:8787/v1"
env_key = "OPENAI_API_KEY"
wire_api = "responses"
```

## Result: it works

A full `codex exec` turn completed through the gateway — request accepted,
pseudonymised, forwarded, streamed answer restored, `response.completed`
delivered, Codex rendered the answer and reported its token count.

Ten requests over two turns, all 200. The gateway's own numbers:

```
{"requests": 10, "entities_per_request": 0.1, "pseudonymize_p50_ms": 32.3,
 "cache": {"hits": 39, "misses": 7, "hit_rate": 0.848, "blocks": 7}}
```

The vault held exactly one entity, `EMAIL`, from the turn that contained one —
so the address was pseudonymised before it reached the model, which is the
claim the whole thing exists to make.

**The cache earned its place here.** Codex resends its ~20 KB instruction
block on every request, and the hit rate across two short turns was already
0.85. PrivaParse's own overhead was 32 ms at the median.

## What the real client sent that the schema did not predict

One field: **`client_metadata`**. It appears in no published schema — it is a
Codex extension — and the first run refused the request with a 502 naming it,
which is the fail-closed rule working exactly as designed.

Its contents were inspected before being waved through, and are identifiers
only: an installation id, session, thread, turn and window ids, and a
timestamp. No paths, no user name, no prompt text. It now sits in the ignored
list beside `prompt_cache_key`, with that reasoning recorded in a test.

Everything else Codex sends was already accounted for: `include`, `input`,
`instructions`, `model`, `parallel_tool_calls`, `prompt_cache_key`,
`reasoning`, `store`, `stream`, `tool_choice`, `tools`.

**Only `message` items were observed.** Qwen-1.5B never invoked a tool, so
`function_call` and `function_call_output` — both implemented and unit-tested
— have still not been seen coming from a real Codex. A capable model would
exercise them on the first turn that touches a file. That gap is real and this
run did not close it.

## Two things worth knowing before trying it

**A small model does not fit.** Codex sends ~20 KB of instructions plus ten
tool definitions before the user has typed anything, which is over 8k tokens.
At `--max-model-len 8192` vLLM rejects every request; 32768 works. This is not
a PrivaParse limit, but it is the first wall anyone pointing Codex at a local
model will hit.

**An upstream error during a stream reads as a hang.** When vLLM rejected the
oversized prompt, the gateway had already sent 200 and its headers, so the
error arrived as body bytes and Codex reported `stream disconnected before
completion: stream closed before response.completed` five times over. The
gateway log said 200 for all of it. That is the known limitation — a streaming
request cannot report an upstream status — meeting a real client, and it makes
a provider-side failure look like a gateway hang. Check the upstream's own log
first.

## Reproducing

Install the package **editable** (`pip install -e ".[gateway]"`). A
non-editable install cost a debugging cycle here: the running gateway imports
from site-packages, so edits to the source tree changed nothing and the old
behaviour persisted through a restart.

```bash
codex exec --skip-git-repo-check "Sag in einem Satz, was in kunde.txt steht."
```
