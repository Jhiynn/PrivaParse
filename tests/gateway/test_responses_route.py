"""`POST /v1/responses` end to end: pseudonymise out, restore back."""

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


def test_the_provider_never_sees_the_name(settings, fake_detector, upstream):
    upstream.reply_for = _echo
    client = _client(settings, fake_detector, upstream)

    client.post(RESPONSES_PATH, json=_ASK)

    sent = upstream.last["input"][0]["content"]
    assert "Max Mustermann" not in sent
    assert "[[PERSON_" in sent


def test_the_answer_comes_back_restored(settings, fake_detector, upstream):
    upstream.reply_for = _echo
    client = _client(settings, fake_detector, upstream)

    answer = client.post(RESPONSES_PATH, json=_ASK).json()

    assert answer["output"][0]["content"][0]["text"] == "Hallo Max Mustermann"


def test_instructions_are_pseudonymised_too(settings, fake_detector, upstream):
    upstream.reply_for = _echo
    client = _client(settings, fake_detector, upstream)

    client.post(RESPONSES_PATH, json={**_ASK, "instructions": "Kunde: Erika Musterfrau"})

    assert "Erika Musterfrau" not in upstream.last["instructions"]


def test_an_unscannable_item_fails_closed(settings, fake_detector, upstream):
    client = _client(settings, fake_detector, upstream)

    response = client.post(RESPONSES_PATH, json={
        "model": "gpt-5-codex",
        "input": [{"type": "computer_call", "action": {"text": "Max Mustermann"}}],
    })

    assert response.status_code == 502
    assert upstream.requests == []


def test_a_request_with_nothing_to_protect_is_forwarded_untouched(
    settings, fake_detector, upstream
):
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


def test_a_streaming_request_is_refused_while_restoration_is_missing(
    settings, fake_detector, upstream
):
    """Codex streams by default, so this is the gap that still blocks it. A
    forwarded stream would pseudonymise correctly and hand back an answer full
    of placeholders -- worse than a refusal, because it looks like it worked."""
    client = _client(settings, fake_detector, upstream)

    response = client.post(RESPONSES_PATH, json={**_ASK, "stream": True})

    assert response.status_code == 501
    assert upstream.requests == []
