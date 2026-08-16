from __future__ import annotations

import io
import json
import re

from starlette.testclient import TestClient

from privaparse.app.logging import configure_logging
from privaparse.gateway.server import create_app

#: Counts a provider might return. Nothing derives them -- the point is that
#: they arrive at the caller as they left the provider.
USAGE = {"prompt_tokens": 17, "completion_tokens": 4, "total_tokens": 21}


def _sent_text(body: dict) -> str:
    """Where Chat Completions puts the text the gateway forwarded."""
    return body["messages"][-1]["content"]


def _reply_with_usage(text: str) -> dict:
    """`text`, said back with counts the caller must see untouched."""
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text},
             "finish_reason": "stop"}
        ],
        "usage": USAGE,
    }


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


def test_one_request_gets_one_mapping(settings, fake_detector, upstream):
    """Every node shares a mapping, or the answer cannot be reversed.

    `reverse()` resolves against exactly one mapping. If the handler
    pseudonymised node by node, each node would open its own mapping and the
    model's answer -- which mixes placeholders from all of them -- could not
    be restored against any single one of them.
    """
    from privaparse.engine import PrivaParseEngine

    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    client = TestClient(create_app(settings, engine=engine, upstream=upstream))
    client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [
                {"role": "user", "content": "Hallo Max Mustermann"},
                {"role": "assistant", "content": "Guten Tag, Max Mustermann"},
                {"role": "user", "content": "Und Erika Musterfrau?"},
            ],
        },
    )
    sent = [message["content"] for message in upstream.last["messages"]]
    placeholder = re.search(r"\[\[PERSON_[^\]]+\]\]", sent[0]).group(0)
    # The same person, the same placeholder, across two separate nodes.
    assert placeholder in sent[1]
    assert len(engine.recent_mappings(limit=10)) == 1


def test_a_body_that_is_not_json_is_refused_before_anything_is_sent(
    settings, fake_detector, upstream
):
    from privaparse.engine import PrivaParseEngine

    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    client = TestClient(create_app(settings, engine=engine, upstream=upstream))
    response = client.post(
        "/v1/chat/completions",
        content=b"{not json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert upstream.requests == []


def test_a_streaming_request_is_answered_as_an_event_stream(
    settings, fake_detector, upstream
):
    """`stream: true` is forwarded and relayed back as SSE. What happens to a
    placeholder split across those events is tests/gateway/test_stream.py."""
    from privaparse.engine import PrivaParseEngine

    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    upstream.chunks = [b"data: [DONE]\n\n"]
    client = TestClient(create_app(settings, engine=engine, upstream=upstream))
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "stream": True,
            "messages": [{"role": "user", "content": "Hallo Max Mustermann"}],
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "Max Mustermann" not in upstream.last["messages"][0]["content"]


def test_an_upstream_status_is_passed_through(settings, fake_detector, upstream):
    """A provider's rate limit is the caller's problem to see, not ours to mask."""
    from privaparse.engine import PrivaParseEngine

    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    upstream.status = 429
    upstream.reply = {"error": {"message": "slow down", "type": "rate_limit_error"}}
    client = TestClient(create_app(settings, engine=engine, upstream=upstream))
    response = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hallo"}]},
    )
    assert response.status_code == 429
    assert response.json()["error"]["type"] == "rate_limit_error"


def test_a_restored_tool_call_is_reserialised_as_json(settings, fake_detector, upstream):
    from privaparse.engine import PrivaParseEngine

    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)

    def reply_for(body):
        # The placeholder this very request issued, put where a model puts it
        # when it decides to call a function. Reversing resolves against one
        # mapping, so it has to come from this request and no other.
        placeholder = re.search(r"\[\[PERSON_[^\]]+\]\]", json.dumps(body)).group(0)
        return {"id": "chatcmpl-2", "choices": [{
            "index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [
                    {"id": "1", "type": "function", "function": {
                        "name": "send",
                        "arguments": json.dumps({"to": placeholder, "count": 1})}}]}}]}

    upstream.reply_for = reply_for
    client = TestClient(create_app(settings, engine=engine, upstream=upstream))
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Schreib an Max Mustermann"}],
        },
    )
    arguments = json.loads(
        response.json()["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    )
    assert arguments["to"] == "Max Mustermann"
    assert arguments["count"] == 1


def test_usage_is_passed_through_untouched(settings, fake_detector, upstream):
    """The counts describe the pseudonymised text, which is the text that was
    billed. Recomputing them against the restored answer would produce a
    number the provider's invoice disagrees with.

    Chat-shaped rather than an invariant: `usage` is this protocol's field, by
    this protocol's names, and the Responses API counts in `input_tokens` /
    `output_tokens` instead.
    """
    from privaparse.engine import PrivaParseEngine

    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    # Echoed so the restore path actually runs over the answer whose counts
    # this asserts are untouched.
    upstream.echo_with(_sent_text, _reply_with_usage)
    client = TestClient(create_app(settings, engine=engine, upstream=upstream))
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hallo Max Mustermann"}],
        },
    )
    assert response.json()["usage"] == USAGE


def test_an_error_body_is_not_mangled_by_the_restore(settings, fake_detector, upstream):
    """A provider error has no `choices`, so the response walk finds nothing
    and hands it back exactly as it came."""
    from privaparse.engine import PrivaParseEngine

    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    upstream.status = 400
    upstream.reply = {"error": {"message": "bad model", "type": "invalid_request_error"}}
    client = TestClient(create_app(settings, engine=engine, upstream=upstream))
    response = client.post(
        "/v1/chat/completions",
        json={"model": "nope", "messages": [{"role": "user", "content": "Hallo Max Mustermann"}]},
    )
    assert response.status_code == 400
    assert response.json() == {"error": {"message": "bad model", "type": "invalid_request_error"}}


def test_the_request_body_is_never_logged(settings, fake_detector, upstream):
    """A refusal logs the pointer. The body it came from must not follow it."""
    from privaparse.engine import PrivaParseEngine

    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    client = TestClient(create_app(settings, engine=engine, upstream=upstream))

    stream = io.StringIO()
    configure_logging("DEBUG", stream=stream)
    client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hallo Max Mustermann"}],
            "some_new_field": "Erika Musterfrau",
        },
    )
    logged = stream.getvalue()
    assert "Max Mustermann" not in logged
    assert "Erika Musterfrau" not in logged


def test_the_gateway_does_not_stash_the_credential_on_app_state(settings, fake_detector, upstream):
    """Narrow on purpose: this checks one specific place a credential could be
    kept around after the request -- app.state. It does not check logging or
    stdout; see test_the_gateway_does_not_log_the_credential for the former.
    """
    from privaparse.engine import PrivaParseEngine

    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    app = create_app(settings, engine=engine, upstream=upstream)
    client = TestClient(app)
    client.get("/v1/models", headers={"authorization": "Bearer sk-secret"})
    assert "sk-secret" not in repr(app.state.__dict__)


def test_the_gateway_does_not_log_the_credential(settings, fake_detector, upstream):
    """The other half of 'no credential is stored, ever': the log stream.

    Uses the same capture pattern as test_logging.py -- an explicit
    StreamHandler wired through configure_logging() -- rather than pytest's
    caplog. caplog attaches at the root logger and only sees records that
    propagate there; configure_logging() sets propagate=False on the
    `privaparse` logger, and once anything in the session calls it (e.g.
    test_logging.py), that stays false for every test that runs after, in
    any order. A caplog-based version of this test would silently stop
    seeing records and pass for the wrong reason -- exactly the kind of test
    this plan is trying to not ship.
    """
    from privaparse.engine import PrivaParseEngine

    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    client = TestClient(create_app(settings, engine=engine, upstream=upstream))

    stream = io.StringIO()
    configure_logging("DEBUG", stream=stream)
    client.get("/v1/models", headers={"authorization": "Bearer sk-secret"})

    assert "sk-secret" not in stream.getvalue()
