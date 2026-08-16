"""The invariants every protocol adapter has to satisfy, asserted over all of them.

`test_protocol_adapter.py` next door asserts the *shape*: that an adapter is a
frozen value, that a route exists for each one and for nothing else. This file
asserts the *behaviour* the route body guarantees on top of whatever protocol
it is pointed at -- each rule written once and run once per adapter.

That is the point of the change #21 made. The two routes drifted because
nothing forced a rule proven on one to be proven on the other; a rule asserted
here cannot drift, because a new adapter runs it the day it joins `ADAPTERS`.

Every rule here was, or would otherwise have been, written twice. The two
route test files carried six of them under near-identical names, which is why
a divergence between the protocols was invisible to the suite as well as to a
reader: two passing tests look the same whether they agree or not. What stays
per-adapter in `test_server.py` and `test_responses_route.py` is what is
genuinely protocol-shaped -- where a hint lands, what a bare-string `input`
does, how a tool call is reserialised.

Fixtures live here rather than on the adapter, because a wire protocol should
not have to carry a request body around in production so a test can find one.
`CONFORMANCE` is keyed by adapter name, and
`test_every_adapter_has_a_conformance_set` fails by name when an adapter has
no entry -- so a third protocol cannot be mounted with no coverage and a green
suite.

The assertions never read a body positionally. A fixture set carries both
sample bodies and the accessors that go with them -- given a forwarded
request, where is the text the gateway sent; given a reply, where is the text
it restored -- so a protocol that puts its text somewhere else supplies an
accessor rather than being reshaped to fit somebody else's field order.

The corpus is the project's own, per CONTRIBUTING.md: `Max Mustermann`,
`Erika Musterfrau`, `beispiel.de`. Nothing here is a real value.
"""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from starlette.testclient import TestClient

from privaparse.app.logging import configure_logging
from privaparse.engine import PrivaParseEngine
from privaparse.gateway.adapter.protocol import ADAPTERS, ProtocolAdapter
from privaparse.gateway.adapter.shared import PLACEHOLDER_HINT
from privaparse.gateway.server import create_app

REAL = "Max Mustermann"
OTHER = "Erika Musterfrau"


# --- what the suite needs in order to speak one protocol -------------------


@dataclass(frozen=True)
class Conformance:
    """One protocol's worth of request bodies and readers.

    Every field is something the suite cannot derive: where a protocol puts
    the text a person typed, what its provider's answer looks like, how its
    stream is framed. Nothing here decides what is *asserted* -- the tests do
    that, once, for all adapters.
    """

    #: One scannable request carrying `REAL`.
    ask: dict
    #: Two separate pieces of text in one request, for the one-mapping rule.
    two_texts: dict
    #: Structurally fine, but carrying text at a pointer the walk has no rule
    #: for. The value is a name, so the same case proves the refusal logs the
    #: pointer and never what was at it.
    unscannable: dict
    #: A request whose content the detector cannot read -- an image part.
    with_image: dict
    #: A request with nothing in it to replace, for the other half of the
    #: hint rule.
    nothing_to_replace: dict
    #: A request carrying no text at all -- not "nothing worth replacing" but
    #: nothing to walk: no text node, so no batch and no mapping either.
    no_text: dict
    #: The text as it was forwarded, read back out of the outbound body. Also
    #: the fake upstream's echo reader.
    sent_text: Callable[[dict], str]
    #: A provider answer carrying that text -- the echo's writer. A real model
    #: repeats a placeholder back constantly, because it looks like a name to
    #: it, and that is the only way a test can see a placeholder it could not
    #: have known in advance.
    answer_with: Callable[[str], dict]
    #: The answer's text, read back out of the provider's reply.
    answered_text: Callable[[dict], str]
    #: A streamed answer that stops mid-candidate: no terminal event, no
    #: `[DONE]`, the connection simply ends while the relay is holding back
    #: characters the caller has already paid for.
    truncated_stream: list[bytes]
    #: The answer text reassembled from a raw SSE body. The flush may arrive
    #: as its own event, so what matters is the concatenation and not whether
    #: any single event carries the characters whole.
    streamed_text: Callable[[str], str]
    #: Whether this protocol's request walk honours `allow_images` yet.
    #: Chat Completions accepts the parameter and ignores it -- see #31.
    honours_allow_images: bool


def _chat_reply(text: str) -> dict:
    """`text`, said back as an assistant message."""
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _chat_sent(body: dict) -> str:
    """The last message's text. Last, not first: the hint is prepended."""
    content = body["messages"][-1]["content"]
    if isinstance(content, list):
        return " ".join(part["text"] for part in content if part.get("type") == "text")
    return content


def _responses_reply(text: str) -> dict:
    return {
        "id": "resp_1",
        "object": "response",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "id": "msg_1",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }


def _responses_sent(body: dict) -> str:
    """The last input item's text. Last, for the same reason as chat."""
    content = body["input"][-1]["content"]
    if isinstance(content, list):
        return " ".join(
            part["text"] for part in content if part.get("type") in {"input_text", "text"}
        )
    return content


def _payloads(raw: str) -> list[Any]:
    """Every `data:` payload in an SSE body, `[DONE]` aside."""
    out = []
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        body = line[len("data: ") :].strip()
        if body == "[DONE]":
            continue
        out.append(json.loads(body))
    return out


def _chat_streamed(raw: str) -> str:
    return "".join(
        payload["choices"][0].get("delta", {}).get("content") or ""
        for payload in _payloads(raw)
        if payload.get("choices")
    )


def _responses_streamed(raw: str) -> str:
    return "".join(
        payload.get("delta") or ""
        for payload in _payloads(raw)
        if payload.get("type") == "response.output_text.delta"
    )


def _responses_event(payload: dict) -> bytes:
    return (
        f"event: {payload['type']}\n".encode()
        + b"data: "
        + json.dumps(payload, ensure_ascii=False).encode()
        + b"\n\n"
    )


#: Every protocol the suite knows how to speak, keyed by its adapter's name --
#: the same name a refusal logs under, so a failing test id and a log line say
#: the same word. A new adapter needs an entry here or
#: `test_every_adapter_has_a_conformance_set` names it.
CONFORMANCE: dict[str, Conformance] = {
    "chat completions": Conformance(
        ask={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": f"Hallo {REAL}"}],
        },
        two_texts={
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": f"Kunde: {OTHER}"},
                {"role": "user", "content": f"Hallo {REAL}"},
            ],
        },
        unscannable={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hallo"}],
            "some_new_field": OTHER,
        },
        with_image={
            "model": "gpt-4o",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Hallo {REAL}"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "https://beispiel.de/bild.png"},
                        },
                    ],
                }
            ],
        },
        nothing_to_replace={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hallo"}],
        },
        no_text={"model": "gpt-4o", "messages": []},
        sent_text=_chat_sent,
        answer_with=_chat_reply,
        answered_text=lambda reply: reply["choices"][0]["message"]["content"],
        truncated_stream=[
            b'data: {"choices":[{"index":0,"delta":{"content":"Ende [["}}]}\n\n'
        ],
        streamed_text=_chat_streamed,
        honours_allow_images=False,
    ),
    "responses": Conformance(
        ask={
            "model": "gpt-5-codex",
            "input": [{"type": "message", "role": "user", "content": f"Hallo {REAL}"}],
        },
        two_texts={
            "model": "gpt-5-codex",
            "instructions": f"Kunde: {OTHER}",
            "input": [{"type": "message", "role": "user", "content": f"Hallo {REAL}"}],
        },
        unscannable={
            "model": "gpt-5-codex",
            "input": [{"type": "message", "role": "user", "content": "Hallo"}],
            "some_new_field": OTHER,
        },
        with_image={
            "model": "gpt-5-codex",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": f"Hallo {REAL}"},
                        {"type": "input_image", "image_url": "https://beispiel.de/bild.png"},
                    ],
                }
            ],
        },
        nothing_to_replace={
            "model": "gpt-5-codex",
            "input": [{"type": "message", "role": "user", "content": "Hallo"}],
        },
        no_text={"model": "gpt-5-codex", "input": []},
        sent_text=_responses_sent,
        answer_with=_responses_reply,
        answered_text=lambda reply: reply["output"][0]["content"][0]["text"],
        truncated_stream=[
            _responses_event(
                {
                    "type": "response.output_text.delta",
                    "delta": "Ende [[",
                    "item_id": "msg_1",
                    "output_index": 0,
                    "content_index": 0,
                    "sequence_number": 1,
                }
            )
        ],
        streamed_text=_responses_streamed,
        honours_allow_images=True,
    ),
}


def _case(adapter: ProtocolAdapter) -> Conformance:
    return CONFORMANCE[adapter.name]


def _built(settings, detector, upstream) -> tuple[TestClient, PrivaParseEngine]:
    """A client and the engine behind it, so a test can reach into either."""
    engine = PrivaParseEngine(settings, detector=detector, configure_logs=False)
    return TestClient(create_app(settings, engine=engine, upstream=upstream)), engine


#: Every adapter, named in the test id by the name it logs under.
adapter = pytest.fixture(params=ADAPTERS, ids=[one.name for one in ADAPTERS])(
    lambda request: request.param
)


# --- the coverage guard ----------------------------------------------------


def test_every_adapter_has_a_conformance_set():
    """A protocol mounted with no conformance coverage fails here, by name.

    The rest of this file parametrises over `ADAPTERS`, so a missing entry
    would already break it -- but as a `KeyError` inside a dozen unrelated
    tests. This one says which adapter and why.
    """
    uncovered = [one.name for one in ADAPTERS if one.name not in CONFORMANCE]
    assert uncovered == [], (
        f"no conformance fixtures for: {', '.join(uncovered)}. "
        "Add an entry to CONFORMANCE keyed by the adapter's name."
    )


# --- the invariants --------------------------------------------------------


def test_it_fails_closed_with_a_pointer_and_no_value(
    settings, fake_detector, upstream, adapter
):
    """Text the walk has no rule for stops the request where it stands.

    Before a byte reaches the provider: a 502 returned after forwarding would
    satisfy a status-code check and leak anyway. What the log and the answer
    carry is the pointer; what tripped it is at neither.
    """
    client, _ = _built(settings, fake_detector, upstream)
    case = _case(adapter)

    stream = io.StringIO()
    configure_logging("DEBUG", stream=stream)
    response = client.post(adapter.path, json=case.unscannable)

    logged = stream.getvalue()
    assert response.status_code == 502
    assert upstream.requests == []
    # The refusal says which protocol made it, so an operator reading a log
    # tells a Codex problem from a chat-client one without reproducing it.
    assert adapter.name in logged
    assert "some_new_field" in logged
    assert OTHER not in logged
    # The caller is told which field stopped their request, and never what was
    # in it -- the pointer travels, the value does not.
    assert "some_new_field" in response.text
    assert OTHER not in response.text


def test_the_provider_never_sees_the_name(settings, fake_detector, upstream, adapter):
    """The half of the round trip that a leak would break.

    Read through the fixture's own accessor rather than by index: the two
    protocols put the text in different places, and an assertion that reached
    into one of them positionally is how a rule came to be proven for one
    adapter while quietly describing nothing on the other.
    """
    client, _ = _built(settings, fake_detector, upstream)
    case = _case(adapter)
    upstream.echo_with(case.sent_text, case.answer_with)

    client.post(adapter.path, json=case.ask)

    sent = case.sent_text(upstream.last)
    assert REAL not in sent
    assert "[[PERSON_" in sent


def test_the_answer_comes_back_restored(settings, fake_detector, upstream, adapter):
    """The other half: the provider saw a placeholder, the caller sees a name.

    Both halves in one test on purpose -- restoring the answer is only worth
    anything if the name never left in the first place.
    """
    client, _ = _built(settings, fake_detector, upstream)
    case = _case(adapter)
    upstream.echo_with(case.sent_text, case.answer_with)

    response = client.post(adapter.path, json=case.ask)

    assert case.answered_text(response.json()) == f"Hallo {REAL}"
    assert REAL not in case.sent_text(upstream.last)


def test_a_body_with_no_text_at_all_is_forwarded_without_a_mapping(
    settings, fake_detector, upstream, adapter
):
    """Nothing to pseudonymise means nothing to record.

    A mapping here would be one that issued no placeholders and can reverse
    nothing -- a row in the most sensitive file the tool produces, standing
    for a request that never needed one.
    """
    client, engine = _built(settings, fake_detector, upstream)
    case = _case(adapter)

    response = client.post(adapter.path, json=case.no_text)

    assert response.status_code == 200
    # Forwarded byte for byte: no hint, no rewrite, nothing added.
    assert upstream.last == case.no_text
    assert engine.recent_mappings(limit=10) == []


def test_one_request_opens_one_mapping(settings, fake_detector, upstream, adapter):
    """However many pieces of text a request holds, they share a mapping.

    Node by node would open a mapping per node, and the answer -- which mixes
    placeholders from all of them -- could not be reversed against any one.
    """
    client, engine = _built(settings, fake_detector, upstream)
    case = _case(adapter)
    upstream.echo_with(case.sent_text, case.answer_with)

    batches: list[list[str]] = []
    real = engine.pseudonymize_batch

    def recording(texts, **kwargs):
        batches.append(list(texts))
        return real(texts, **kwargs)

    engine.pseudonymize_batch = recording
    client.post(adapter.path, json=case.two_texts)

    assert len(batches) == 1
    # Both pieces went into it, rather than one being pseudonymised alone.
    assert len(batches[0]) >= 2


def test_the_hint_is_added_once_and_only_when_something_was_replaced(
    settings, fake_detector, upstream, adapter
):
    """Exactly once, and never on a request with nothing to protect.

    Twice would bill the caller twice for it. On a request where nothing was
    replaced it is tokens spent to explain placeholders that are not there.
    """
    hinted = settings.model_copy(update={"gateway_hint": True})
    client, _ = _built(hinted, fake_detector, upstream)
    case = _case(adapter)
    upstream.echo_with(case.sent_text, case.answer_with)

    client.post(adapter.path, json=case.ask)
    with_entities = json.dumps(upstream.last, ensure_ascii=False)

    client.post(adapter.path, json=case.nothing_to_replace)
    without = json.dumps(upstream.last, ensure_ascii=False)

    assert with_entities.count(PLACEHOLDER_HINT) == 1
    assert PLACEHOLDER_HINT not in without


def test_gateway_allow_images_is_honoured(settings, fake_detector, upstream, adapter):
    """One setting, one meaning, on every route the gateway serves.

    An operator sets `gateway_allow_images` once and it is documented as a
    global rule. The Chat Completions walk does not implement it yet (#31);
    that gap is asserted rather than skipped, so the day it closes this test
    fails and the flag comes out with the branch.
    """
    permissive = settings.model_copy(update={"gateway_allow_images": True})
    client, _ = _built(permissive, fake_detector, upstream)
    case = _case(adapter)
    upstream.echo_with(case.sent_text, case.answer_with)

    response = client.post(adapter.path, json=case.with_image)

    if case.honours_allow_images:
        assert response.status_code == 200
        # Forwarded unexamined, which is what the operator asked for -- and
        # the text alongside it was still pseudonymised.
        assert REAL not in json.dumps(upstream.last, ensure_ascii=False)
    else:
        assert response.status_code == 502
        assert upstream.requests == []


@pytest.mark.parametrize("streaming", [False, True], ids=["buffered", "streaming"])
def test_detection_being_unavailable_is_refused_with_500_and_the_guidance(
    settings, upstream, monkeypatch, adapter, streaming
):
    """A server-side misconfiguration only an operator can fix.

    A remote client cannot read the server log, so an uncaught RuntimeError
    from the detector build must not surface as a bare 500 -- it comes back as
    the error envelope, carrying the install guidance. 500 and not 503: 503
    invites an OpenAI-compatible client to retry a condition that will not
    resolve on its own.

    Both stream branches, because detection runs before the route body ever
    branches on `stream` -- and a client that never turns streaming off (Codex
    does not) would otherwise get a broken event-stream instead of the
    envelope.

    `gliner2` is actually installed here; its absence is simulated the way
    `tests/test_detector.py` does it, by blocking the import through
    `sys.modules`. No `fake_detector` is injected -- the whole point is to let
    the engine build its own detector lazily on the first request and hit the
    real, unpatched `_build_gliner_detector`.
    """
    monkeypatch.setitem(sys.modules, "gliner2", None)
    hybrid = settings.model_copy(update={"detector": "hybrid"})
    engine = PrivaParseEngine(hybrid, configure_logs=False)
    client = TestClient(create_app(hybrid, engine=engine, upstream=upstream))
    case = _case(adapter)

    response = client.post(adapter.path, json={**case.ask, "stream": streaming})

    assert response.status_code == 500
    error = response.json()["error"]
    # The literal wire string, not the constant: this is what a client's own
    # error handling switches on, so a rename has to fail here.
    assert error["type"] == "privaparse_model_unavailable"
    assert "pip install -e '.[model]'" in error["message"]
    assert "--detector regex" in error["message"]
    # Fails closed, same as the refusal above: nothing reached the provider.
    assert upstream.requests == []


def test_restoration_never_aborts(settings, fake_detector, upstream, adapter):
    """The answer already exists and has already been paid for.

    A failure outbound risks disclosure, so that half fails closed. A failure
    inbound costs readability, so this half hands the caller the answer with
    its placeholders standing rather than an error.
    """
    client, engine = _built(settings, fake_detector, upstream)
    case = _case(adapter)
    upstream.echo_with(case.sent_text, case.answer_with)

    def broken(*args, **kwargs):
        raise RuntimeError("the vault went away")

    engine.reverse = broken
    response = client.post(adapter.path, json=case.ask)

    assert response.status_code == 200
    # Placeholders standing, rather than a 500 -- and still not the real name.
    text = case.answered_text(response.json())
    assert "[[PERSON_" in text
    assert REAL not in text


def test_a_stream_that_ends_without_a_terminal_event_loses_nothing(
    settings, fake_detector, upstream, adapter
):
    """The relay holds back characters that might begin a placeholder.

    When the stream simply stops -- no terminal event, no `[DONE]` -- those
    characters were paid for and are still the caller's. They have to be
    flushed rather than dropped with the hold-back buffer.
    """
    client, _ = _built(settings, fake_detector, upstream)
    case = _case(adapter)
    upstream.chunks_for = lambda body: case.truncated_stream

    response = client.post(adapter.path, json={**case.ask, "stream": True})

    assert response.status_code == 200
    # Reassembled, not contiguous: the held-back `[[` is flushed as its own
    # event. What must not happen is those characters going missing.
    assert case.streamed_text(response.text) == "Ende [["
