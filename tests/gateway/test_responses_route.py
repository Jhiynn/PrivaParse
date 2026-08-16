"""What `POST /v1/responses` does that no other protocol does.

The rules this route shares with every other one -- failing closed, the round
trip, one mapping, the hint, detection being unavailable -- are asserted once
in `test_adapter_conformance.py` and run against this adapter from there. What
is left here is protocol-shaped: a top-level `instructions` string, a bare
string `input`, the hint arriving as an input item, and the typed event
stream.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from privaparse.engine import PrivaParseEngine
from privaparse.gateway.server import RESPONSES_PATH, create_app

_ASK = {
    "model": "gpt-5-codex",
    "instructions": "Du hilfst beim Code.",
    "input": [{"type": "message", "role": "user", "content": "Hallo Max Mustermann"}],
}


def _client(settings, detector, upstream) -> TestClient:
    engine = PrivaParseEngine(settings, detector=detector, configure_logs=False)
    return TestClient(create_app(settings, engine=engine, upstream=upstream))


def _echo(body: dict) -> dict:
    """A provider that answers with whatever text it was sent."""
    sent = body["input"]
    if isinstance(sent, list):
        sent = sent[-1]["content"]
    if isinstance(sent, list):
        sent = sent[0]["text"]
    return {
        "id": "resp_1", "object": "response", "status": "completed",
        "output": [{"type": "message", "role": "assistant", "id": "msg_1",
                    "content": [{"type": "output_text", "text": sent, "annotations": []}]}],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }


def test_instructions_are_pseudonymised_too(settings, fake_detector, upstream):
    upstream.reply_for = _echo
    client = _client(settings, fake_detector, upstream)

    client.post(RESPONSES_PATH, json={**_ASK, "instructions": "Kunde: Erika Musterfrau"})

    assert "Erika Musterfrau" not in upstream.last["instructions"]


def test_a_bare_string_input_is_forwarded_as_a_bare_string(
    settings, fake_detector, upstream
):
    """`input` is a union whose first member is a plain string.

    Protocol-shaped, not an invariant: what is under test is that a string
    survives as a string rather than being reshaped into a list of items on
    the way through. Chat Completions has no equivalent -- `messages` is
    always a list.
    """
    upstream.reply_for = _echo
    client = _client(settings, fake_detector, upstream)

    client.post(RESPONSES_PATH, json={"model": "gpt-5-codex", "input": "wie spaet ist es?"})

    assert upstream.last["input"] == "wie spaet ist es?"


def test_the_hint_is_prepended_as_an_input_item(settings, fake_detector, upstream):
    upstream.reply_for = _echo
    hinted = settings.model_copy(update={"gateway_hint": True})
    client = _client(hinted, fake_detector, upstream)

    client.post(RESPONSES_PATH, json=_ASK)

    first = upstream.last["input"][0]
    assert first["role"] == "system"
    assert "[[" in first["content"]


def test_a_streamed_answer_is_restored(settings, fake_detector, upstream):
    """Codex streams by default, so this is the path that matters for it."""
    import json as _json

    def chunks_for(body):
        sent = body["input"][-1]["content"]
        placeholder = sent[sent.index("[["):sent.index("]]") + 2]
        out = []
        for character in f"Hallo {placeholder}":
            out.append(
                b"data: " + _json.dumps({
                    "type": "response.output_text.delta", "delta": character,
                    "item_id": "msg_1", "output_index": 0, "content_index": 0,
                    "sequence_number": 1,
                }).encode() + b"\n\n"
            )
        out.append(
            b"data: " + _json.dumps({
                "type": "response.output_text.done", "item_id": "msg_1",
                "output_index": 0, "content_index": 0, "sequence_number": 9,
                "text": f"Hallo {placeholder}",
            }).encode() + b"\n\n"
        )
        return out

    upstream.chunks_for = chunks_for
    client = _client(settings, fake_detector, upstream)

    response = client.post(RESPONSES_PATH, json={**_ASK, "stream": True})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    text = "".join(
        _json.loads(line[6:]).get("delta", "")
        for block in response.text.split("\n\n")
        for line in [block.strip()]
        if line.startswith("data: ")
        and _json.loads(line[6:]).get("type") == "response.output_text.delta"
    )
    assert text == "Hallo Max Mustermann"
    assert "Max Mustermann" not in upstream.last["input"][0]["content"]
