"""Streaming restoration: a placeholder arrives split across events.

The hold-back is the whole idea. `[[PERSON_A1]]` reaches the gateway as any
number of pieces -- `[[PER`, `SON_`, `A1]]` -- and a restorer that looked at
each piece alone would find nothing to restore in any of them and hand the
caller a placeholder. So the tail of the buffer is held back until it either
completes or can no longer become a placeholder.

There is no pytest-asyncio in this project, so the async generators are driven
with `asyncio.run` rather than an async test function.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from starlette.testclient import TestClient

from privaparse.app.catalogue import load_catalogue
from privaparse.engine import PrivaParseEngine
from privaparse.gateway.server import create_app
from privaparse.gateway.stream import HoldBack, max_placeholder_length, restore_sse

PLACEHOLDER = "[[PERSON_A1]]"
REAL = "Max Mustermann"


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
    return text.replace(PLACEHOLDER, REAL)


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


# --- the hold-back ---------------------------------------------------------


@pytest.mark.parametrize("cut", range(1, len(PLACEHOLDER)))
def test_a_placeholder_is_never_released_in_two_pieces(cut: int):
    """The property the whole design rests on: whatever the split, no release
    ever contains part of a placeholder and not the rest."""
    hold = HoldBack(max_hold=40)
    released = [hold.feed(PLACEHOLDER[:cut]), hold.feed(PLACEHOLDER[cut:]), hold.flush()]

    assert "".join(released) == PLACEHOLDER
    for piece in released:
        assert PLACEHOLDER in piece or "[[" not in piece


def test_nothing_is_held_when_no_bracket_appears():
    hold = HoldBack(max_hold=40)
    assert hold.feed("Hallo, alles gut?") == "Hallo, alles gut?"
    assert hold.flush() == ""


def test_a_bracket_that_cannot_become_a_placeholder_is_released_at_once():
    """Markdown, a wiki link, a Python list of lists: `[[` is not rare."""
    hold = HoldBack(max_hold=40)
    assert hold.feed("siehe [[dies hier]]") == "siehe [[dies hier]]"


def test_a_trailing_bracket_is_held_until_it_is_settled():
    hold = HoldBack(max_hold=40)
    assert hold.feed("Hallo [[") == "Hallo "
    assert hold.feed("wer?") == "[[wer?"


def test_an_endless_run_of_capitals_is_released_at_the_cap():
    """Held text that stays grammatical forever would stall the stream. The cap
    is what bounds it -- past the longest placeholder the vault can build, the
    text cannot be one."""
    hold = HoldBack(max_hold=20)
    assert hold.feed("[[" + "A" * 30) == "[[" + "A" * 30


def test_flush_returns_a_half_finished_placeholder_rather_than_dropping_it():
    hold = HoldBack(max_hold=40)
    assert hold.feed("Hallo [[PERSON_") == "Hallo "
    assert hold.flush() == "[[PERSON_"


def test_the_cap_covers_the_longest_placeholder_the_catalogue_can_render():
    catalogue = load_catalogue()
    longest = max(len(placeholder.name) for placeholder in catalogue.enabled)
    assert max_placeholder_length(catalogue) > longest + len("[[_A1]]")


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
    boundary = whole.index("ü".encode("utf-8")) + 1
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
