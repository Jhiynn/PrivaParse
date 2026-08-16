"""The Chat Completions relay: a placeholder arrives split across events.

The hold-back is the whole idea, and it is tested on its own in
`test_restore.py` -- `[[PERSON_A1]]` reaches the gateway as any number of
pieces, and a restorer that looked at each piece alone would find nothing to
restore in any of them. What is tested here is what this protocol's relay does
with that: where a released piece rides, which chunk carries a held tail, and
how a tool call assembled from fragments comes back out.

There is no pytest-asyncio in this project, so the async generators are driven
with `asyncio.run` rather than an async test function.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from starlette.testclient import TestClient

from privaparse.engine import PrivaParseEngine
from privaparse.gateway.server import create_app
from privaparse.gateway.stream import restore_sse

PLACEHOLDER = "[[PERSON_A1]]"
REAL = "Max Mustermann"
EMAIL_PLACEHOLDER = "[[EMAIL_A2]]"
EMAIL = "max@test.de"
#: A value that cannot survive being pasted into serialised JSON. Restoring a
#: tool call by string substitution would produce `"Max "Maxi" Mustermann"`
#: and break the arguments the client is about to execute.
QUOTED_PLACEHOLDER = "[[PERSON_A3]]"
QUOTED = 'Max "Maxi" Mustermann'

_MAPPING = {
    PLACEHOLDER: REAL,
    EMAIL_PLACEHOLDER: EMAIL,
    QUOTED_PLACEHOLDER: QUOTED,
}


# --- helpers ---------------------------------------------------------------


def _event(payload: dict) -> bytes:
    # `ensure_ascii=False`, because a provider sends real UTF-8 rather than
    # `ü` escapes -- which is what puts a character across a byte
    # boundary in the first place.
    return b"data: " + json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n\n"


def _chunk(text: str | None = None, *, index: int = 0, finish: str | None = None) -> dict:
    delta: dict = {} if text is None else {"content": text}
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "choices": [{"index": index, "delta": delta, "finish_reason": finish}],
    }


async def _feed(pieces):
    for piece in pieces:
        yield piece


async def _swap(text: str) -> str:
    for placeholder, real in _MAPPING.items():
        text = text.replace(placeholder, real)
    return text


def _run(pieces, restore=_swap, max_hold: int = 40) -> bytes:
    async def drive() -> bytes:
        out = []
        async for piece in restore_sse(_feed(pieces), restore=restore, max_hold=max_hold):
            out.append(piece)
        return b"".join(out)

    return asyncio.run(drive())


def _choices(raw: bytes) -> list[dict]:
    """Every choice in the output stream, in the order it was emitted."""
    out: list[dict] = []
    for block in raw.decode("utf-8").split("\n\n"):
        line = block.strip()
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        out.extend(json.loads(line[len("data: "):]).get("choices", []))
    return out


def _content(raw: bytes) -> str:
    """Every delta content in the output stream, joined back together."""
    pieces = [choice.get("delta", {}).get("content") for choice in _choices(raw)]
    return "".join(piece for piece in pieces if isinstance(piece, str))


# --- the event stream ------------------------------------------------------


@pytest.mark.parametrize("cut", range(1, len(PLACEHOLDER)))
def test_a_placeholder_split_across_two_events_is_restored(cut: int):
    raw = _run([
        _event(_chunk("Hallo ")),
        _event(_chunk(PLACEHOLDER[:cut])),
        _event(_chunk(PLACEHOLDER[cut:])),
        _event(_chunk(None, finish="stop")),
        b"data: [DONE]\n\n",
    ])
    assert _content(raw) == f"Hallo {REAL}"


def test_a_placeholder_split_one_character_per_event_is_restored():
    raw = _run(
        [_event(_chunk(character)) for character in PLACEHOLDER]
        + [_event(_chunk(None, finish="stop")), b"data: [DONE]\n\n"]
    )
    assert _content(raw) == REAL


def test_a_placeholder_at_the_very_end_is_restored():
    """Nothing follows it, so its release depends on the closing `]]` alone."""
    raw = _run([_event(_chunk(f"Gruss {PLACEHOLDER}")), _event(_chunk(None, finish="stop"))])
    assert _content(raw) == f"Gruss {REAL}"


def test_the_finish_chunk_carries_whatever_is_still_held():
    """An answer ending on `[[` holds a candidate no later delta will complete.
    It has to ride on the chunk that closes the choice, not on one after it: a
    client is entitled to stop reading a choice at `finish_reason`, and text
    emitted past that point is text the caller never sees."""
    raw = _run([_event(_chunk("Ende [[")), _event(_chunk(None, finish="stop"))])

    assert _content(raw) == "Ende [["
    finished = False
    for choice in _choices(raw):
        assert not (finished and choice.get("delta", {}).get("content"))
        finished = finished or choice.get("finish_reason") is not None


def test_a_stream_that_ends_mid_placeholder_loses_nothing():
    raw = _run([_event(_chunk("Hallo [[PERSON_"))])
    assert _content(raw) == "Hallo [[PERSON_"


def test_a_dropped_connection_still_delivers_what_was_held():
    """The same stop, arriving as an exception rather than as bytes running out.

    A provider connection that dies mid-answer surfaces here as a raised error,
    and the held characters were paid for on that path too. The error still has
    to travel: an answer cut short must not be reported as a whole one.
    """
    async def dropping():
        yield _event(_chunk("Hallo [[PERSON_"))
        raise RuntimeError("the connection went away")

    out: list[bytes] = []

    async def drive():
        async for piece in restore_sse(dropping(), restore=_swap, max_hold=40):
            out.append(piece)

    with pytest.raises(RuntimeError):
        asyncio.run(drive())

    assert _content(b"".join(out)) == "Hallo [[PERSON_"


def test_an_empty_stream_produces_nothing():
    assert _run([]) == b""


def test_the_done_sentinel_survives():
    raw = _run([_event(_chunk("hi")), b"data: [DONE]\n\n"])
    assert raw.endswith(b"data: [DONE]\n\n")


def test_an_event_split_across_byte_chunks_is_reassembled():
    """The transport chops bytes wherever it likes; an SSE event is not a
    chunk."""
    whole = _event(_chunk(f"Hallo {PLACEHOLDER}")) + _event(_chunk(None, finish="stop"))
    raw = _run([whole[:17], whole[17:40], whole[40:]])
    assert _content(raw) == f"Hallo {REAL}"


def test_a_multibyte_character_split_across_byte_chunks_survives():
    whole = _event(_chunk("Grüße")) + _event(_chunk(None, finish="stop"))
    boundary = whole.index("ü".encode()) + 1
    raw = _run([whole[:boundary], whole[boundary:]])
    assert _content(raw) == "Grüße"


def test_two_choices_hold_back_separately():
    """`n=2` interleaves two answers. One choice's tail is not the other's."""
    raw = _run([
        _event(_chunk(PLACEHOLDER[:5], index=0)),
        _event(_chunk("nichts", index=1)),
        _event(_chunk(PLACEHOLDER[5:], index=0)),
        _event(_chunk(None, index=0, finish="stop")),
        _event(_chunk(None, index=1, finish="stop")),
    ])
    assert _content(raw) == f"nichts{REAL}"


def test_a_chunk_carrying_only_usage_passes_through():
    usage = {"id": "chatcmpl-1", "choices": [], "usage": {"total_tokens": 9}}
    raw = _run([_event(_chunk("hi")), _event(usage)])
    assert b'"total_tokens": 9' in raw or b'"total_tokens":9' in raw


def test_an_event_that_is_not_json_passes_through_untouched():
    raw = _run([b"data: not json at all\n\n"])
    assert raw == b"data: not json at all\n\n"


def test_a_comment_line_passes_through_untouched():
    raw = _run([b": keep-alive\n\n"])
    assert raw == b": keep-alive\n\n"


def test_a_delta_without_a_placeholder_never_reaches_the_vault():
    """A token-by-token stream would otherwise hit the database once per
    token, for text that plainly holds nothing to restore."""
    asked: list[str] = []

    async def restore(text: str) -> str:
        asked.append(text)
        return text

    _run([_event(_chunk("Hallo, alles gut?")), _event(_chunk(None, finish="stop"))],
         restore=restore)

    assert asked == []


def test_a_restore_failure_leaves_the_placeholder_standing_and_the_stream_alive():
    async def exploding(text: str) -> str:
        raise RuntimeError("the vault is unavailable")

    raw = _run([_event(_chunk(f"Hallo {PLACEHOLDER}")), _event(_chunk(None, finish="stop"))],
               restore=exploding)

    assert _content(raw) == f"Hallo {PLACEHOLDER}"


# --- tool calls ------------------------------------------------------------


def _call(index: int = 0, *, id: str | None = None, name: str | None = None,
          arguments: str | None = None) -> dict:
    """One tool-call fragment, shaped the way a provider streams them: the id
    and the name arrive with the first, the arguments dribble in after."""
    fragment: dict = {"index": index}
    if id is not None:
        fragment["id"] = id
        fragment["type"] = "function"
    function: dict = {}
    if name is not None:
        function["name"] = name
    if arguments is not None:
        function["arguments"] = arguments
    if function:
        fragment["function"] = function
    return fragment


def _tools(*calls: dict, index: int = 0, finish: str | None = None) -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "choices": [
            {"index": index, "delta": {"tool_calls": list(calls)}, "finish_reason": finish}
        ],
    }


def _tool_calls(raw: bytes) -> list[dict]:
    out: list[dict] = []
    for choice in _choices(raw):
        out.extend(choice.get("delta", {}).get("tool_calls") or [])
    return out


def test_a_tool_call_split_across_five_events_arrives_as_one():
    raw = _run([
        _event(_tools(_call(id="call_1", name="send", arguments='{"to": "'))),
        _event(_tools(_call(arguments="[[EMAIL"))),
        _event(_tools(_call(arguments="_A2]]"))),
        _event(_tools(_call(arguments='", "cop'))),
        _event(_tools(_call(arguments='ies": 3}'))),
        _event(_chunk(None, finish="tool_calls")),
    ])

    calls = _tool_calls(raw)
    assert len(calls) == 1
    assert calls[0]["id"] == "call_1"
    assert calls[0]["function"]["name"] == "send"
    assert json.loads(calls[0]["function"]["arguments"]) == {"to": EMAIL, "copies": 3}


def test_nothing_of_a_tool_call_is_relayed_before_it_is_complete():
    """A fragment on its own is unparseable, so restoring it is impossible and
    forwarding it is pointless -- a partial tool call is not executable."""
    raw = _run([
        _event(_tools(_call(id="call_1", name="send", arguments='{"to": "[[EMA'))),
        _event(_tools(_call(arguments='IL_A2]]"}'))),
        _event(_chunk(None, finish="tool_calls")),
    ])

    carried = [bool(choice.get("delta", {}).get("tool_calls")) for choice in _choices(raw)]
    assert carried == [False, False, True]


def test_two_tool_calls_at_different_indices_do_not_interleave():
    raw = _run([
        _event(_tools(
            _call(0, id="a", name="first", arguments='{"x": "'),
            _call(1, id="b", name="second", arguments='{"y": "'),
        )),
        _event(_tools(
            _call(0, arguments='[[EMAIL_A2]]"}'),
            _call(1, arguments='[[PERSON_A1]]"}'),
        )),
        _event(_chunk(None, finish="tool_calls")),
    ])

    calls = _tool_calls(raw)
    assert [call["index"] for call in calls] == [0, 1]
    assert json.loads(calls[0]["function"]["arguments"]) == {"x": EMAIL}
    assert json.loads(calls[1]["function"]["arguments"]) == {"y": REAL}


def test_a_restored_value_is_re_serialised_rather_than_pasted_in():
    """The reason the arguments are parsed instead of string-replaced. A name
    holding a quote would otherwise produce JSON the client cannot parse."""
    raw = _run([
        _event(_tools(_call(id="a", name="send", arguments='{"name": "[[PERSON_A3]]"}'))),
        _event(_chunk(None, finish="tool_calls")),
    ])

    arguments = _tool_calls(raw)[0]["function"]["arguments"]
    assert json.loads(arguments) == {"name": QUOTED}


def test_an_argument_key_is_left_alone():
    """Keys are parameter names from the client's own schema, never anything a
    user typed -- the same rule the non-streaming walk follows."""
    raw = _run([
        _event(_tools(_call(id="a", name="send", arguments='{"[[PERSON_A1]]": 1}'))),
        _event(_chunk(None, finish="tool_calls")),
    ])

    assert json.loads(_tool_calls(raw)[0]["function"]["arguments"]) == {PLACEHOLDER: 1}


def test_a_tool_call_rides_on_the_chunk_that_finishes_the_choice():
    raw = _run([
        _event(_tools(_call(id="a", name="send", arguments='{"to": "[[EMAIL_A2]]"}'))),
        _event(_chunk(None, finish="tool_calls")),
    ])

    finished = False
    for choice in _choices(raw):
        assert not (finished and choice.get("delta", {}).get("tool_calls"))
        finished = finished or choice.get("finish_reason") is not None


def test_a_stream_that_stops_before_finishing_still_delivers_the_tool_call():
    raw = _run([_event(_tools(_call(id="a", name="send", arguments='{"to": "[[EMAIL_A2]]"}')))])

    assert json.loads(_tool_calls(raw)[0]["function"]["arguments"]) == {"to": EMAIL}


def test_a_truncated_tool_call_still_gets_its_placeholders_back():
    """Arguments that never became valid JSON are restored as plain text. The
    client gets a broken tool call either way; it should not also get one with
    a placeholder in it."""
    raw = _run([
        _event(_tools(_call(id="a", name="send", arguments='{"to": "[[EMAIL_A2]]'))),
        _event(_chunk(None, finish="tool_calls")),
    ])

    assert _tool_calls(raw)[0]["function"]["arguments"] == '{"to": "' + EMAIL


def test_content_and_a_tool_call_in_one_stream_both_come_through():
    raw = _run([
        _event(_chunk(f"Ich schreibe {PLACEHOLDER}")),
        _event(_tools(_call(id="a", name="send", arguments='{"to": "[[EMAIL_A2]]"}'))),
        _event(_chunk(None, finish="tool_calls")),
    ])

    assert _content(raw) == f"Ich schreibe {REAL}"
    assert json.loads(_tool_calls(raw)[0]["function"]["arguments"]) == {"to": EMAIL}


def test_a_restore_failure_still_delivers_the_tool_call():
    async def exploding(text: str) -> str:
        raise RuntimeError("the vault is unavailable")

    raw = _run([
        _event(_tools(_call(id="a", name="send", arguments='{"to": "[[EMAIL_A2]]"}'))),
        _event(_chunk(None, finish="tool_calls")),
    ], restore=exploding)

    assert json.loads(_tool_calls(raw)[0]["function"]["arguments"]) == {"to": EMAIL_PLACEHOLDER}


# --- through the gateway ---------------------------------------------------


def _client(settings, detector, upstream) -> TestClient:
    engine = PrivaParseEngine(settings, detector=detector, configure_logs=False)
    return TestClient(create_app(settings, engine=engine, upstream=upstream))


def test_a_streaming_request_is_pseudonymised_before_it_is_forwarded(
    settings, fake_detector, upstream
):
    client = _client(settings, fake_detector, upstream)
    upstream.chunks = [_event(_chunk("ok")), b"data: [DONE]\n\n"]

    client.post("/v1/chat/completions", json={
        "model": "gpt-4o",
        "stream": True,
        "messages": [{"role": "user", "content": "Hallo Max Mustermann"}],
    })

    sent = upstream.last["messages"][0]["content"]
    assert "Max Mustermann" not in sent
    assert "[[PERSON_" in sent


def test_a_streamed_answer_comes_back_restored(settings, fake_detector, upstream):
    """End to end: the fake provider echoes the placeholder back one character
    at a time, which is what a real model does with a token it does not
    recognise."""
    client = _client(settings, fake_detector, upstream)

    def chunks_for(body):
        placeholder = body["messages"][0]["content"].split("Hallo ")[1]
        return [_event(_chunk(character)) for character in f"Hallo {placeholder}"] + [
            _event(_chunk(None, finish="stop")),
            b"data: [DONE]\n\n",
        ]

    upstream.chunks_for = chunks_for
    response = client.post("/v1/chat/completions", json={
        "model": "gpt-4o",
        "stream": True,
        "messages": [{"role": "user", "content": "Hallo Max Mustermann"}],
    })

    assert response.status_code == 200
    assert _content(response.content) == "Hallo Max Mustermann"


def test_a_streamed_answer_still_arrives_with_a_key_configured(
    settings, fake_detector, upstream
):
    """The risk the pure-ASGI middleware exists to avoid: a
    `BaseHTTPMiddleware` wraps the response object, and wrapping a
    `StreamingResponse` is a well-known way to break it. This runs the same
    streamed, restored request as `test_a_streamed_answer_comes_back_restored`
    through an app with a key configured, presenting it, and checks the
    chunks arrive exactly as they do without one.
    """
    key = "s3cret-stream-key"
    client = _client(settings.model_copy(update={"api_key": key}), fake_detector, upstream)

    def chunks_for(body):
        placeholder = body["messages"][0]["content"].split("Hallo ")[1]
        return [_event(_chunk(character)) for character in f"Hallo {placeholder}"] + [
            _event(_chunk(None, finish="stop")),
            b"data: [DONE]\n\n",
        ]

    upstream.chunks_for = chunks_for
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "stream": True,
            "messages": [{"role": "user", "content": "Hallo Max Mustermann"}],
        },
        headers={"X-PrivaParse-Key": key},
    )

    assert response.status_code == 200
    assert _content(response.content) == "Hallo Max Mustermann"


def test_a_streaming_request_still_fails_closed(settings, fake_detector, upstream):
    client = _client(settings, fake_detector, upstream)

    response = client.post("/v1/chat/completions", json={
        "model": "gpt-4o",
        "stream": True,
        "messages": [{"role": "user", "content": "hallo"}],
        "some_new_field": "Max Mustermann",
    })

    assert response.status_code == 502
    assert upstream.requests == []
