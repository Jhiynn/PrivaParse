"""The protocol adapter as a value, and the one route body it is served through.

What each protocol does with a request is asserted next door, in
`test_server.py` and `test_responses_route.py`. What this file asserts is the
shape: that an adapter is a frozen value, that a route exists for every one of
them and for no protocol that is not one, that the catalogue is measured when
the app is built rather than once per streamed answer, and that a third
protocol needs nothing but an entry in the tuple.

The rules the route body then guarantees on top of any adapter -- failing
closed, one mapping per request, the hint, `allow_images`, the 500, the
restore, the stream flush -- are asserted once each and run against every
adapter in `test_adapter_conformance.py`.

The third adapter here is a fake, mounted for the length of one test. It is the
point of the change rather than a convenience: if adding a protocol is a value,
a value is all a test should have to add.
"""

from __future__ import annotations

import dataclasses
import io

import pytest
from starlette.routing import Route
from starlette.testclient import TestClient

from privaparse.app.logging import configure_logging
from privaparse.engine import PrivaParseEngine
from privaparse.gateway import server
from privaparse.gateway.adapter.protocol import ADAPTERS, ProtocolAdapter
from privaparse.gateway.extract import TextNode, UnscannableField
from privaparse.gateway.server import create_app


def _client(settings, detector, upstream, **kwargs) -> TestClient:
    engine = PrivaParseEngine(settings, detector=detector, configure_logs=False)
    return TestClient(create_app(settings, engine=engine, upstream=upstream, **kwargs))


def test_every_adapter_is_mounted_at_its_own_path(settings, fake_detector, upstream):
    """One route per entry in the tuple, at the path the adapter names."""
    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    app = create_app(settings, engine=engine, upstream=upstream)

    mounted = [
        route.path
        for route in app.routes
        if isinstance(route, Route) and "POST" in (route.methods or ())
    ]

    for adapter in ADAPTERS:
        assert mounted.count(adapter.path) == 1


def test_no_protocol_is_served_that_is_not_an_adapter(settings, fake_detector, upstream):
    """The other half: a route the tuple does not know about cannot exist.

    Anything under `/v1` is a provider protocol. `/privaparse/...` is the
    direct API, which is PrivaParse's own surface rather than a wire protocol
    it speaks, so it is deliberately not in this comparison.
    """
    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    app = create_app(settings, engine=engine, upstream=upstream)

    served = {
        route.path
        for route in app.routes
        if isinstance(route, Route)
        and "POST" in (route.methods or ())
        and route.path.startswith("/v1")
    }

    assert served == {adapter.path for adapter in ADAPTERS}


def test_the_catalogue_is_measured_when_the_app_is_built(settings, fake_detector, upstream):
    """Once per process, not once per streamed answer.

    The catalogue is fixed for the life of the app, so measuring the longest
    placeholder it can render is a constant. It used to be recomputed on every
    streaming request, which walked the whole catalogue to arrive at the same
    number each time.
    """
    measured: list[object] = []
    real = server.max_placeholder_length

    def counting(catalogue):
        measured.append(catalogue)
        return real(catalogue)

    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(server, "max_placeholder_length", counting)
        client = TestClient(create_app(settings, engine=engine, upstream=upstream))
        built = len(measured)

        upstream.chunks = [
            b'data: {"choices":[{"index":0,"delta":{"content":"ok"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        for _ in range(2):
            client.post("/v1/chat/completions", json={
                "model": "gpt-4o",
                "stream": True,
                "messages": [{"role": "user", "content": "Hallo Max Mustermann"}],
            })

    assert built == 1
    assert len(measured) == 1


def test_an_adapter_cannot_be_edited_once_the_app_is_running(settings):
    """A frozen value. A route that could be re-pointed at runtime would make
    the mounted set something other than what the tuple says it is."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        ADAPTERS[0].path = "/v1/somewhere-else"


# --- a third protocol is a value -------------------------------------------


def _fake_adapter() -> ProtocolAdapter:
    """A protocol whose whole definition is this function.

    It speaks a body of `{"say": "..."}`, refuses anything else, and answers
    with `{"heard": "..."}`. Nothing about it is realistic except its shape,
    which is the part under test.
    """

    def request_walk(body, *, allow_images: bool) -> list[TextNode]:
        if not isinstance(body, dict) or "say" not in body:
            raise UnscannableField(("say",), "expected something to say")
        return [TextNode(("say",), body["say"])]

    def answer_walk(body) -> list[TextNode]:
        if isinstance(body, dict) and isinstance(body.get("heard"), str):
            return [TextNode(("heard",), body["heard"])]
        return []

    def hint_insertion(body: dict) -> dict:
        return {**body, "hint": True}

    async def stream_relay(chunks, *, restore, max_hold: int, lenient: bool):
        async for chunk in chunks:
            yield chunk

    return ProtocolAdapter(
        name="fake",
        path="/v1/fake",
        request_walk=request_walk,
        answer_walk=answer_walk,
        hint_insertion=hint_insertion,
        stream_relay=stream_relay,
    )


def test_a_third_protocol_is_served_by_adding_it_to_the_tuple(
    settings, fake_detector, upstream
):
    """The whole point of the shape: no route was written for this protocol."""
    fake = _fake_adapter()
    upstream.reply_for = lambda body: {"heard": body["say"]}

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(server, "ADAPTERS", (*ADAPTERS, fake))
        client = _client(settings, fake_detector, upstream)

        answer = client.post("/v1/fake", json={"say": "Hallo Max Mustermann"})

    # Pseudonymised on the way out, restored on the way back -- both halves of
    # the route body, over an adapter that supplied only its five slots.
    assert "Max Mustermann" not in upstream.last["say"]
    assert "[[PERSON_" in upstream.last["say"]
    assert answer.json() == {"heard": "Hallo Max Mustermann"}


def test_a_third_protocol_fails_closed_without_writing_that_rule_again(
    settings, fake_detector, upstream
):
    """The refusal path is inherited too, log line included."""
    fake = _fake_adapter()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(server, "ADAPTERS", (*ADAPTERS, fake))
        client = _client(settings, fake_detector, upstream)

        stream = io.StringIO()
        configure_logging("DEBUG", stream=stream)
        response = client.post("/v1/fake", json={"nothing": "to say"})

    assert response.status_code == 502
    assert upstream.requests == []
    assert "fake" in stream.getvalue()
