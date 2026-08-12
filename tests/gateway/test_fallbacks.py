"""The two opt-in answers to a model that will not echo a placeholder.

Both are off by default and both are measured rather than assumed --
docs/gateway-model-fidelity-report.md has the numbers that motivated them.

`PRIVAPARSE_GATEWAY_FUZZY` widens what counts as a placeholder on the way
back. `PRIVAPARSE_GATEWAY_HINT` adds a system message on the way out asking
the model to reproduce them verbatim. They are independent because they fail
differently: the first cannot recover a placeholder the model dropped
entirely, and the second cannot guarantee anything at all.
"""

from __future__ import annotations

import json

from starlette.testclient import TestClient

from privaparse.app.config import Settings
from privaparse.engine import PrivaParseEngine
from privaparse.gateway.server import create_app


def _client(settings: Settings, detector, upstream) -> TestClient:
    engine = PrivaParseEngine(settings, detector=detector, configure_logs=False)
    return TestClient(create_app(settings, engine=engine, upstream=upstream))


def _mangle(body: dict) -> dict:
    """Answer with the placeholder the request carried, one bracket short."""
    sent = body["messages"][0]["content"]
    placeholder = sent[sent.index("[["):sent.index("]]") + 2]
    return {
        "id": "chatcmpl-1", "object": "chat.completion",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant",
                                 "content": f"Antwort an {placeholder[1:-1]}."}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


_ASK = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hallo Max Mustermann"}]}


# --- fuzzy restoration -----------------------------------------------------


def test_a_mangled_answer_stays_mangled_by_default(settings, fake_detector, upstream):
    upstream.reply_for = _mangle
    client = _client(settings, fake_detector, upstream)

    answer = client.post("/v1/chat/completions", json=_ASK).json()

    assert answer["choices"][0]["message"]["content"] == "Antwort an [PERSON_A1]."


def test_fuzzy_restores_the_mangled_answer(settings, fake_detector, upstream):
    upstream.reply_for = _mangle
    fuzzy = settings.model_copy(update={"gateway_fuzzy": True})
    client = _client(fuzzy, fake_detector, upstream)

    answer = client.post("/v1/chat/completions", json=_ASK).json()

    assert answer["choices"][0]["message"]["content"] == "Antwort an Max Mustermann."


def test_fuzzy_restores_a_mangled_placeholder_in_a_stream(
    settings, fake_detector, upstream
):
    """The hold-back only protects `[[`, so a single-bracket form would
    otherwise be split across events and never match anything."""
    def chunks_for(body):
        sent = body["messages"][0]["content"]
        placeholder = sent[sent.index("[["):sent.index("]]") + 2][1:-1]
        out = []
        for character in f"Antwort an {placeholder}.":
            out.append(
                b"data: " + json.dumps({
                    "id": "c", "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"content": character},
                                 "finish_reason": None}],
                }).encode() + b"\n\n"
            )
        out.append(
            b"data: " + json.dumps({
                "id": "c", "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }).encode() + b"\n\n"
        )
        return out

    upstream.chunks_for = chunks_for
    fuzzy = settings.model_copy(update={"gateway_fuzzy": True})
    client = _client(fuzzy, fake_detector, upstream)

    response = client.post("/v1/chat/completions", json={**_ASK, "stream": True})

    content = "".join(
        choice.get("delta", {}).get("content", "")
        for block in response.text.split("\n\n")
        if block.strip().startswith("data: ") and block.strip() != "data: [DONE]"
        for choice in json.loads(block.strip()[6:]).get("choices", [])
    )
    assert content == "Antwort an Max Mustermann."


# --- the outbound hint -----------------------------------------------------


def test_no_hint_is_sent_by_default(settings, fake_detector, upstream):
    client = _client(settings, fake_detector, upstream)

    client.post("/v1/chat/completions", json=_ASK)

    assert [m["role"] for m in upstream.last["messages"]] == ["user"]


def test_the_hint_is_prepended_as_a_system_message(settings, fake_detector, upstream):
    hinted = settings.model_copy(update={"gateway_hint": True})
    client = _client(hinted, fake_detector, upstream)

    client.post("/v1/chat/completions", json=_ASK)

    messages = upstream.last["messages"]
    assert messages[0]["role"] == "system"
    assert "[[" in messages[0]["content"]
    assert messages[1]["content"].startswith("Hallo [[PERSON_")


def test_the_hint_is_not_sent_when_the_request_carried_no_entities(
    settings, fake_detector, upstream
):
    """Nothing to protect, so nothing to ask for -- and the caller keeps the
    tokens."""
    hinted = settings.model_copy(update={"gateway_hint": True})
    client = _client(hinted, fake_detector, upstream)

    client.post("/v1/chat/completions", json={
        "model": "gpt-4o", "messages": [{"role": "user", "content": "Wie spaet ist es?"}],
    })

    assert [m["role"] for m in upstream.last["messages"]] == ["user"]


def test_the_hint_is_never_scanned_or_stored(settings, fake_detector, upstream):
    """It is added after pseudonymisation, so it cannot reach the detector and
    cannot land in the vault -- it would be an entity nobody typed."""
    hinted = settings.model_copy(update={"gateway_hint": True})
    engine = PrivaParseEngine(hinted, detector=fake_detector, configure_logs=False)
    client = TestClient(create_app(hinted, engine=engine, upstream=upstream))

    client.post("/v1/chat/completions", json=_ASK)

    hint = upstream.last["messages"][0]["content"]
    assert "[[PERSON_" not in hint
    assert engine.vault_stats().entities == 1


def test_the_hint_survives_alongside_the_caller_s_own_system_prompt(
    settings, fake_detector, upstream
):
    hinted = settings.model_copy(update={"gateway_hint": True})
    client = _client(hinted, fake_detector, upstream)

    client.post("/v1/chat/completions", json={
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "Du bist knapp."},
            {"role": "user", "content": "Hallo Max Mustermann"},
        ],
    })

    messages = upstream.last["messages"]
    assert [m["role"] for m in messages] == ["system", "system", "user"]
    assert messages[1]["content"] == "Du bist knapp."


def test_both_switches_are_off_in_a_default_configuration():
    assert Settings().gateway_fuzzy is False
    assert Settings().gateway_hint is False
