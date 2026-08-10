# Local Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** an OpenAI-compatible endpoint on `127.0.0.1` that pseudonymises requests on the way out and restores answers on the way back, so any client accepting a `base_url` override gets PrivaParse without changing a line of its own code.

**Architecture:** a Starlette app in a new `privaparse/gateway/` package. A protocol adapter turns request JSON into `(pointer, text)` pairs; the core never sees OpenAI's shape. One `pseudonymize_batch` call per request gives every node one mapping, which is what makes the answer reversible. Detection is cached per message block keyed by content hash plus a catalogue fingerprint. Streaming responses are restored through a hold-back buffer because a placeholder arrives split across events.

**Tech Stack:** Python 3.11+, Starlette, uvicorn, httpx, pydantic v2, pytest. No new dependency on the detection side.

## Global Constraints

- Python `>=3.11`. `from __future__ import annotations` at the top of every module.
- Line length 100 (`[tool.ruff]`, `target-version = "py311"`). Ruff is not installed in the dev environment — check by reading.
- The default `pytest` run stays under a few seconds and needs no model weights. Gateway tests use a fake detector and a fake upstream; nothing in this plan may require a GPU.
- **The gateway calls `PrivaParseEngine`, never a detector directly.** Raw model output scores 0.438 precision where the same output through `resolve_spans` scores 0.753. A gateway that skips the engine ships four times the false positives and looks fine doing it.
- **The request path fails closed, the response path never aborts.** A failure outbound risks disclosure; a failure inbound shows a placeholder.
- Never log or `repr()` an entity value, a request body, or an `Authorization` header. `register_secret()` is the existing mechanism for values.
- **No provider credentials are stored, ever.** The client's `Authorization` header is forwarded and never written anywhere.
- The server binds `127.0.0.1` only. Binding `0.0.0.0` would expose a global plaintext vault to every user on the network; if a task appears to need it, stop and report.
- **No attribution trailers.** No `Co-Authored-By`, no "Generated with", no mention of or link to Claude, Claude Code or Anthropic — not in commit messages, not in code comments, not in docs.
- Conventional Commits in plain English prose. Every task ends with a commit.

---

## File Structure

**Created:**

| File | Responsibility |
| --- | --- |
| `privaparse/gateway/__init__.py` | Package marker; re-exports `create_app`. |
| `privaparse/gateway/server.py` | Starlette app, routes, engine lifecycle. |
| `privaparse/gateway/extract.py` | JSON walk to `(pointer, text)`, write-back, fail-closed field whitelist. |
| `privaparse/gateway/adapter/openai.py` | OpenAI request and response shapes; the only module that knows them. |
| `privaparse/gateway/cache.py` | Content-hash to detection spans, LRU, catalogue fingerprint. |
| `privaparse/gateway/stream.py` | SSE restoration with hold-back; tool-call buffering. |
| `privaparse/gateway/upstream.py` | httpx client, header passthrough, stream relay. |
| `tests/gateway/test_extract.py` | Walker, write-back, unknown-field rejection. |
| `tests/gateway/test_cache.py` | Hit, miss, catalogue-fingerprint invalidation. |
| `tests/gateway/test_stream.py` | Split placeholders, tool calls, flush. |
| `tests/gateway/test_server.py` | End-to-end against a fake upstream. |
| `tests/gateway/conftest.py` | Fake upstream, fake engine, app fixture. |

**Modified:** `pyproject.toml` (a `gateway` extra), `privaparse/app/main.py` (`serve`, `run`, `gateway stats`), `README.md` (last task only).

---

### Task 1: Server skeleton, health and model passthrough

Nothing is pseudonymised yet. This lands the process, the routes and the upstream client, so every later task has somewhere to attach.

**Files:**
- Create: `privaparse/gateway/__init__.py`, `server.py`, `upstream.py`
- Modify: `pyproject.toml`
- Test: `tests/gateway/conftest.py`, `tests/gateway/test_server.py`

**Interfaces:**
- Produces: `create_app(settings, engine=None) -> Starlette`; `Upstream(base_url, timeout)` with `.post_json(path, body, headers) -> tuple[int, dict, dict]` and `.stream(path, body, headers) -> AsyncIterator[bytes]`.
- Consumes: `Settings`, `PrivaParseEngine`.

- [ ] **Step 1: Add the extra**

In `pyproject.toml`, under `[project.optional-dependencies]`:

```toml
gateway = ["starlette>=0.37", "uvicorn>=0.30", "httpx>=0.27"]
```

Install with `pip install -e ".[dev,gateway]"`.

- [ ] **Step 2: Write the failing tests**

`tests/gateway/conftest.py` gives every later task its fake upstream. It must record what it received, because most assertions in this plan are about what left the machine:

```python
from __future__ import annotations

import json

import pytest


class FakeUpstream:
    """Stands in for the provider. Records every request it was handed."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        self.reply: dict = {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "ok"},
                 "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        self.chunks: list[bytes] = []

    async def post_json(self, path, body, headers):
        self.requests.append(json.loads(json.dumps(body)))
        self.headers.append(dict(headers))
        return 200, self.reply, {"content-type": "application/json"}

    async def stream(self, path, body, headers):
        self.requests.append(json.loads(json.dumps(body)))
        self.headers.append(dict(headers))
        for chunk in self.chunks:
            yield chunk

    @property
    def last(self) -> dict:
        return self.requests[-1]


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream()
```

`tests/gateway/test_server.py`:

```python
from __future__ import annotations

from starlette.testclient import TestClient

from privaparse.gateway.server import create_app


def test_healthz_reports_ready(settings, fake_detector, upstream):
    from privaparse.engine import PrivaParseEngine

    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    client = TestClient(create_app(settings, engine=engine, upstream=upstream))
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_models_is_proxied_upstream_untouched(settings, fake_detector, upstream):
    from privaparse.engine import PrivaParseEngine

    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    upstream.reply = {"object": "list", "data": [{"id": "gpt-4o"}]}
    client = TestClient(create_app(settings, engine=engine, upstream=upstream))
    response = client.get("/v1/models", headers={"authorization": "Bearer sk-test"})
    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "gpt-4o"
    # The key belongs to the caller and must reach the provider unchanged.
    assert upstream.headers[-1]["authorization"] == "Bearer sk-test"


def test_the_gateway_stores_no_credential(settings, fake_detector, upstream):
    from privaparse.engine import PrivaParseEngine

    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    app = create_app(settings, engine=engine, upstream=upstream)
    client = TestClient(app)
    client.get("/v1/models", headers={"authorization": "Bearer sk-secret"})
    assert "sk-secret" not in repr(app.state.__dict__)
```

- [ ] **Step 3: Run them, confirm they fail**

Run: `pytest tests/gateway -v`
Expected: FAIL, `ModuleNotFoundError: privaparse.gateway`.

- [ ] **Step 4: Write `upstream.py`**

A thin httpx wrapper. It forwards `authorization` and `content-type` and nothing else — a header the gateway does not understand is a header it should not relay.

```python
"""The only place that talks to the provider.

Headers are forwarded by allow-list rather than passed through wholesale: a
header the gateway does not understand could carry routing or caching semantics
it has no way to reason about, and the failure would be silent.
"""

from __future__ import annotations

from typing import AsyncIterator

import httpx

_FORWARDED = ("authorization", "content-type", "openai-organization", "openai-project")


class Upstream:
    def __init__(self, base_url: str, timeout: float = 600.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    @staticmethod
    def _headers(incoming) -> dict[str, str]:
        return {k: v for k, v in incoming.items() if k.lower() in _FORWARDED}

    async def post_json(self, path, body, headers):
        response = await self._client.post(
            f"{self.base_url}{path}", json=body, headers=self._headers(headers)
        )
        return response.status_code, response.json(), dict(response.headers)

    async def get_json(self, path, headers):
        response = await self._client.get(
            f"{self.base_url}{path}", headers=self._headers(headers)
        )
        return response.status_code, response.json(), dict(response.headers)

    async def stream(self, path, body, headers) -> AsyncIterator[bytes]:
        async with self._client.stream(
            "POST", f"{self.base_url}{path}", json=body, headers=self._headers(headers)
        ) as response:
            async for chunk in response.aiter_bytes():
                yield chunk

    async def aclose(self) -> None:
        await self._client.aclose()
```

- [ ] **Step 5: Write `server.py`**

`create_app(settings, engine=None, upstream=None)` — both injectable so tests need neither a model nor a network. `/healthz` returns `{"status": "ready"}` once the engine exists. `/v1/models` proxies. `/v1/chat/completions` returns 501 for now with a body saying the next task fills it in; a stub that forwards would be a hole that looks like progress.

- [ ] **Step 6: Run the tests**

Run: `pytest tests/gateway -v`
Expected: PASS.

- [ ] **Step 7: Full suite**

Run: `pytest`
Expected: PASS, no regressions.

- [ ] **Step 8: Commit**

```bash
git add privaparse/gateway tests/gateway pyproject.toml
git commit -m "feat: gateway process, health check and model passthrough

The routes and the upstream client, with nothing pseudonymised yet, so the
later tasks have somewhere to attach. Headers are forwarded by allow-list:
a header the gateway does not understand could carry routing semantics it
cannot reason about, and that failure would be silent.

/v1/chat/completions answers 501 rather than forwarding. A stub that
forwarded would be a hole that looked like progress."
```

---

### Task 2: The extraction seam

The module that decides what leaves the machine. Everything else in this plan is plumbing around it.

**Files:**
- Create: `privaparse/gateway/extract.py`, `privaparse/gateway/adapter/__init__.py`, `privaparse/gateway/adapter/openai.py`
- Test: `tests/gateway/test_extract.py`

**Interfaces:**
- Produces: `TextNode(pointer: tuple, text: str)`; `extract(body) -> list[TextNode]`; `write_back(body, nodes, replacements) -> dict`; `UnscannableField(Exception)` carrying the pointer and the reason.

- [ ] **Step 1: Write the failing tests**

Cover the shapes that actually occur, and the rejections that matter:

```python
from __future__ import annotations

import pytest

from privaparse.gateway.extract import UnscannableField, extract, write_back


def test_string_content_is_found():
    body = {"messages": [{"role": "user", "content": "Max Mustermann"}]}
    assert [n.text for n in extract(body)] == ["Max Mustermann"]


def test_array_content_finds_every_text_part():
    body = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "erste"},
        {"type": "text", "text": "zweite"},
    ]}]}
    assert [n.text for n in extract(body)] == ["erste", "zweite"]


def test_tool_call_arguments_are_walked_as_json():
    body = {"messages": [{"role": "assistant", "tool_calls": [
        {"id": "1", "type": "function", "function": {
            "name": "send", "arguments": '{"to": "max@test.de", "count": 3}'}}
    ]}]}
    found = [n.text for n in extract(body)]
    assert "max@test.de" in found


def test_a_number_leaf_in_tool_arguments_is_walked_too():
    """A phone number arrives as a JSON number often enough to matter."""
    body = {"messages": [{"role": "assistant", "tool_calls": [
        {"id": "1", "type": "function", "function": {
            "name": "dial", "arguments": '{"number": 4917012345}'}}
    ]}]}
    assert "4917012345" in [n.text for n in extract(body)]


def test_an_unknown_field_carrying_a_string_is_refused():
    body = {"messages": [{"role": "user", "content": "hallo"}],
            "some_new_field": "Max Mustermann"}
    with pytest.raises(UnscannableField) as excinfo:
        extract(body)
    assert "some_new_field" in str(excinfo.value)


def test_an_image_part_is_refused():
    body = {"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBOR"}}
    ]}]}
    with pytest.raises(UnscannableField):
        extract(body)


def test_known_non_text_fields_are_ignored_without_complaint():
    body = {"model": "gpt-4o", "temperature": 0.7, "stream": True,
            "messages": [{"role": "user", "content": "hallo"}]}
    assert [n.text for n in extract(body)] == ["hallo"]


def test_write_back_round_trips_and_reserialises_tool_arguments():
    body = {"messages": [{"role": "assistant", "tool_calls": [
        {"id": "1", "type": "function", "function": {
            "name": "send", "arguments": '{"to": "max@test.de"}'}}
    ]}]}
    nodes = extract(body)
    out = write_back(body, nodes, ["[[EMAIL_A1]]"])
    import json
    assert json.loads(
        out["messages"][0]["tool_calls"][0]["function"]["arguments"]
    )["to"] == "[[EMAIL_A1]]"
    # The original must not be mutated: a failure downstream has to leave the
    # caller's body untouched.
    assert "max@test.de" in body["messages"][0]["tool_calls"][0]["function"]["arguments"]
```

- [ ] **Step 2: Run them, confirm they fail**

Run: `pytest tests/gateway/test_extract.py -v`
Expected: FAIL, module missing.

- [ ] **Step 3: Write `extract.py`**

Two allow-lists and one rule. `_TEXT_FIELDS` names what is walked; `_IGNORED` names non-text fields known to be safe (`model`, `temperature`, `stream`, `max_tokens`, `top_p`, `n`, `stop`, `presence_penalty`, `frequency_penalty`, `seed`, `user`, `logprobs`, `response_format`, `tool_choice`, `parallel_tool_calls`, `stream_options`). Anything else that contains a string, anywhere in the tree, raises `UnscannableField` naming its pointer.

`write_back` returns a deep copy. Tool-call arguments are re-serialised with `json.dumps` after substitution; a number leaf that was pseudonymised comes back as a string, which is correct — a placeholder is not a number.

- [ ] **Step 4: Write `adapter/openai.py`**

It owns the two lists and the response-side pointers (`choices[].message.content`, `choices[].message.tool_calls[].function.arguments`). `extract.py` holds the walking; the adapter holds the knowledge of the shape. Keeping them apart is what makes a second protocol a new file.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/gateway/test_extract.py -v`
Expected: PASS.

- [ ] **Step 6: Full suite, then commit**

```bash
git add privaparse/gateway tests/gateway
git commit -m "feat: walk a request for text, and refuse what cannot be walked

An allow-list of text-bearing fields, an allow-list of known non-text ones,
and a hard refusal for anything else carrying a string. Tool-call arguments
are parsed as JSON and walked to their leaves, numbers included -- a phone
number arrives as a JSON number often enough to matter.

The refusal will break when a provider adds a field. That is the trade: a
gateway that forwards what it does not understand leaks the first time a
client adopts a new API feature, and does it silently."
```

---

### Task 3: The request path

**Files:**
- Modify: `privaparse/gateway/server.py`
- Test: `tests/gateway/test_server.py`

**Interfaces:**
- Consumes: `extract`, `write_back`, `PrivaParseEngine.pseudonymize_batch`, `Upstream`.
- Produces: a working non-streaming `POST /v1/chat/completions` that pseudonymises before forwarding.

- [ ] **Step 1: Write the failing tests**

The assertion that matters is about the fake upstream's recorded body — what actually left:

```python
def test_the_provider_never_sees_the_name(settings, fake_detector, upstream):
    from privaparse.engine import PrivaParseEngine
    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    client = TestClient(create_app(settings, engine=engine, upstream=upstream))
    client.post("/v1/chat/completions", json={
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hallo Max Mustermann"}],
    }, headers={"authorization": "Bearer sk-test"})
    sent = upstream.last["messages"][0]["content"]
    assert "Max Mustermann" not in sent
    assert "[[PERSON_" in sent


def test_one_request_gets_one_mapping(settings, fake_detector, upstream):
    """Every node shares a mapping, or the answer cannot be reversed."""
    ...


def test_an_unscannable_field_is_refused_with_502_and_nothing_is_sent(
    settings, fake_detector, upstream
):
    ...
    assert response.status_code == 502
    assert upstream.requests == []
```

That last assertion — `upstream.requests == []` — is the one that proves fail-closed. A 502 returned *after* forwarding would satisfy a status-code check and leak anyway.

- [ ] **Step 2: Run, confirm failure. Step 3: implement. Step 4: run again.**

The handler: extract, `engine.pseudonymize_batch([n.text for n in nodes])`, `write_back`, forward, restore (Task 4 completes the restore; for now return the upstream body untouched and assert only on the request side).

- [ ] **Step 5: Full suite, then commit**

---

### Task 4: The response path, non-streaming

**Files:** `privaparse/gateway/server.py`, `adapter/openai.py`; test in `test_server.py`.

- [ ] Restore `choices[].message.content` and tool-call arguments with `engine.reverse(mapping_id, text)`.
- [ ] **A restoration failure must not fail the request.** Log it, return the answer with placeholders standing. Add a test that forces `reverse` to raise and asserts the response is still 200 with the placeholder visible.
- [ ] `usage` passes through untouched. Add a test asserting the numbers are byte-identical to what the upstream returned, with a comment giving the reason: the counts describe the pseudonymised text, which is the text that was billed.
- [ ] Full suite, commit.

---

### Task 5: The detection cache

**Files:** Create `privaparse/gateway/cache.py`; modify `server.py`; test `tests/gateway/test_cache.py`.

- [ ] **The key is `(catalogue_fingerprint, sha256(text))`.** A cached span set is only valid for the catalogue that produced it, and the catalogue is a file an operator edits. Derive the fingerprint from the resolved catalogue — the enabled types, their labels, thresholds and validators — not from the file's mtime.
- [ ] Only detection is cached. Resolution and the mapping entry run every request, which is what keeps `reverse`'s session scoping exactly as sharp as the CLI's.
- [ ] LRU with a configurable cap (`PRIVAPARSE_GATEWAY_CACHE`, default 2048 blocks).
- [ ] Tests: a hit returns without calling the detector; changing a threshold in the catalogue invalidates; eviction works; a cached block still produces a fresh mapping entry.
- [ ] Full suite, commit.

---

### Task 6: Streaming restoration

The hardest task in the plan. A placeholder arrives split across events.

**Files:** Create `privaparse/gateway/stream.py`; modify `server.py`; test `tests/gateway/test_stream.py`.

- [ ] **Step 1: Write the failing tests first, and make them adversarial.** Split `[[PERSON_A1]]` at every one of its character boundaries and assert the restored output is identical each time. A test that splits it once proves almost nothing.
- [ ] Also test: text containing `[[` that is not a placeholder (must be released once the buffer passes the longest possible placeholder); a placeholder at the very end with no trailing delta; an empty stream; a stream that ends mid-placeholder.
- [ ] **Step 2: Implement the hold-back.** Append to a buffer; find the last unclosed `[[`; restore and emit everything before it; hold the rest. Release the hold when the held text exceeds the longest placeholder the vault can produce.
- [ ] The restorer is per-stream and holds the request's `mapping_id`.
- [ ] Full suite, commit.

---

### Task 7: Tool calls in streams

- [ ] `delta.tool_calls[].function.arguments` arrives as JSON fragments, unparseable until complete. Buffer per `index` until `finish_reason`, then parse, restore, emit as one piece.
- [ ] Test: a tool call split across five events restores correctly; two concurrent tool calls at different indices do not interleave.
- [ ] Nothing is lost by not streaming them: a partial tool call is not executable.
- [ ] Full suite, commit.

---

### Task 8: `serve` and `run`

**Files:** `privaparse/app/main.py`; test `tests/test_cli.py`.

- [ ] `privaparse serve [--port 8787]` — uvicorn on `127.0.0.1`. Refuse a non-loopback host with an error explaining that the vault holds plaintext.
- [ ] `privaparse run -- <command>` — probe `/healthz`; start the daemon if absent; set `OPENAI_BASE_URL` in the child environment; pass `OPENAI_API_KEY` through unchanged; run the child; propagate its exit code. On Windows there is no `exec`, so use `subprocess` and forward signals.
- [ ] `privaparse gateway stats` — requests, cache hit rate, entities per request, p50 latency. **Never content.**
- [ ] Tests: `run` injects the variable and propagates a non-zero exit; `serve` refuses `0.0.0.0`.
- [ ] Full suite, commit.

---

### Task 9: Packaging and documentation

- [ ] Dockerfile with `:slim` and `:full` targets, the latter baking the weights and setting `PRIVAPARSE_OFFLINE=1`.
- [ ] README: the one-command activation, the client list, and — plainly — that restoration puts real PII into the client, so pointing a *server-side* client such as Open WebUI at this places restored answers on that host.
- [ ] Record the known `TAX_ID` recall defect in the gateway section too. Someone reading only that section must still meet it.
- [ ] **[lab]** Measure first-turn latency against a realistic coding-agent payload — 50 KB and 200 KB — and record it. The spec names this as unmeasured, and shipping without the number would repeat the mistake this project spent a branch correcting.
- [ ] Full suite, commit.

---

## Self-Review

**Spec coverage.** Server and routes → Task 1. Extraction and fail-closed → Task 2. Request path and one-mapping-per-request → Task 3. Response path and its never-abort rule → Task 4. Cache with catalogue fingerprint → Task 5. Streaming → Task 6. Tool calls → Task 7. `serve`/`run`/stats → Task 8. Packaging, README and the latency measurement → Task 9.

**One thing deliberately deferred.** The spec names the Anthropic adapter as the highest-value follow-on. It is not in this plan: the `extract.py` seam exists to make it a separate change, and adding it here would mean two protocols before either is proven against a real client.

**The assertion to watch.** Task 3's `assert upstream.requests == []` is the one that proves fail-closed. Every later task that touches the request path must keep it passing; a reviewer should check it has not been weakened to a status-code assertion.
