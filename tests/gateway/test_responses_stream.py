"""Restoring a Responses stream, which is not a chat stream with new names.

Chat sends anonymous chunks carrying `choices[].delta`. The Responses API
sends *typed* events -- `response.output_text.delta` with `{delta, item_id,
output_index, content_index}` -- and repeats every completed value again in a
`.done` event and once more inside `response.completed`. So there are three
places the same text can arrive, and a client may read any one of them.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from privaparse.gateway.stream_responses import restore_responses_sse

PLACEHOLDER = "[[PERSON_A1]]"
REAL = "Max Mustermann"
EMAIL_PLACEHOLDER = "[[EMAIL_A2]]"
EMAIL = "max@test.de"
_MAP = {PLACEHOLDER: REAL, EMAIL_PLACEHOLDER: EMAIL}


def _event(payload: dict) -> bytes:
    """Both lines, the way the API sends them."""
    return (
        f"event: {payload['type']}\n".encode()
        + b"data: " + json.dumps(payload, ensure_ascii=False).encode() + b"\n\n"
    )


def _delta(text: str, *, item: str = "msg_1", content: int = 0) -> dict:
    return {"type": "response.output_text.delta", "delta": text, "item_id": item,
            "output_index": 0, "content_index": content, "sequence_number": 1}


async def _feed(pieces):
    for piece in pieces:
        yield piece


async def _swap(text: str) -> str:
    for key, value in _MAP.items():
        text = text.replace(key, value)
    return text


def _run(pieces, restore=_swap, max_hold: int = 40) -> bytes:
    async def drive() -> bytes:
        out = []
        async for piece in restore_responses_sse(
            _feed(pieces), restore=restore, max_hold=max_hold
        ):
            out.append(piece)
        return b"".join(out)

    return asyncio.run(drive())


def _payloads(raw: bytes) -> list[dict]:
    out = []
    for block in raw.decode("utf-8").split("\n\n"):
        for line in block.strip().split("\n"):
            if line.startswith("data: ") and line != "data: [DONE]":
                out.append(json.loads(line[6:]))
    return out


def _text(raw: bytes) -> str:
    return "".join(
        p.get("delta", "") for p in _payloads(raw)
        if p.get("type") == "response.output_text.delta"
    )


# --- text ------------------------------------------------------------------


@pytest.mark.parametrize("cut", range(1, len(PLACEHOLDER)))
def test_a_placeholder_split_across_deltas_is_restored(cut: int):
    raw = _run([
        _event(_delta("Hallo ")),
        _event(_delta(PLACEHOLDER[:cut])),
        _event(_delta(PLACEHOLDER[cut:])),
        _event({"type": "response.output_text.done", "item_id": "msg_1",
                "output_index": 0, "content_index": 0, "sequence_number": 9,
                "text": f"Hallo {PLACEHOLDER}"}),
    ])
    assert _text(raw) == f"Hallo {REAL}"


def test_the_done_event_carries_the_restored_full_text():
    """A client may ignore every delta and read only this."""
    raw = _run([
        _event(_delta(f"Hallo {PLACEHOLDER}")),
        _event({"type": "response.output_text.done", "item_id": "msg_1",
                "output_index": 0, "content_index": 0, "sequence_number": 9,
                "text": f"Hallo {PLACEHOLDER}"}),
    ])
    done = [p for p in _payloads(raw) if p["type"] == "response.output_text.done"]
    assert done[0]["text"] == f"Hallo {REAL}"


def test_a_tail_still_held_at_done_is_emitted_before_it():
    """An answer ending mid-candidate must not lose those characters, and they
    have to arrive before the event that closes the text."""
    raw = _run([
        _event(_delta("Ende [[")),
        _event({"type": "response.output_text.done", "item_id": "msg_1",
                "output_index": 0, "content_index": 0, "sequence_number": 9,
                "text": "Ende [["}),
    ])
    assert _text(raw) == "Ende [["
    kinds = [p["type"] for p in _payloads(raw)]
    assert kinds.index("response.output_text.delta") < kinds.index(
        "response.output_text.done"
    )


def test_two_output_items_hold_back_separately():
    raw = _run([
        _event(_delta(PLACEHOLDER[:6], item="msg_1")),
        _event(_delta("nichts", item="msg_2")),
        _event(_delta(PLACEHOLDER[6:], item="msg_1")),
    ])
    assert _text(raw) == f"nichts{REAL}"


# --- tool calls ------------------------------------------------------------


def test_function_call_arguments_are_held_until_complete():
    raw = _run([
        _event({"type": "response.function_call_arguments.delta", "item_id": "fc_1",
                "output_index": 0, "sequence_number": 1, "delta": '{"to": "'}),
        _event({"type": "response.function_call_arguments.delta", "item_id": "fc_1",
                "output_index": 0, "sequence_number": 2, "delta": "[[EMAIL_A2]]"}),
        _event({"type": "response.function_call_arguments.delta", "item_id": "fc_1",
                "output_index": 0, "sequence_number": 3, "delta": '"}'}),
        _event({"type": "response.function_call_arguments.done", "item_id": "fc_1",
                "output_index": 0, "sequence_number": 4,
                "arguments": '{"to": "[[EMAIL_A2]]"}'}),
    ])

    deltas = [p for p in _payloads(raw)
              if p["type"] == "response.function_call_arguments.delta"]
    done = [p for p in _payloads(raw)
            if p["type"] == "response.function_call_arguments.done"]
    # One delta, complete, rather than three fragments -- a partial tool call
    # is not executable and a fragment cannot be restored.
    assert len(deltas) == 1
    assert json.loads(deltas[0]["delta"]) == {"to": EMAIL}
    assert json.loads(done[0]["arguments"]) == {"to": EMAIL}


def test_tool_arguments_are_re_serialised_rather_than_pasted():
    async def quoting(text: str) -> str:
        return text.replace(EMAIL_PLACEHOLDER, 'Max "Maxi" Mustermann')

    raw = _run([
        _event({"type": "response.function_call_arguments.done", "item_id": "fc_1",
                "output_index": 0, "sequence_number": 4,
                "arguments": '{"to": "[[EMAIL_A2]]"}'}),
    ], restore=quoting)

    done = [p for p in _payloads(raw)
            if p["type"] == "response.function_call_arguments.done"]
    assert json.loads(done[0]["arguments"]) == {"to": 'Max "Maxi" Mustermann'}


# --- the terminal event ----------------------------------------------------


def test_the_completed_event_has_its_whole_response_restored():
    """`response.completed` repeats the entire answer. A client that reads only
    this one would otherwise see every placeholder standing."""
    raw = _run([
        _event({"type": "response.completed", "sequence_number": 20, "response": {
            "id": "resp_1", "object": "response", "status": "completed",
            "output": [{"type": "message", "role": "assistant", "id": "msg_1",
                        "content": [{"type": "output_text",
                                     "text": f"Hallo {PLACEHOLDER}"}]}],
        }}),
    ])
    completed = [p for p in _payloads(raw) if p["type"] == "response.completed"][0]
    assert completed["response"]["output"][0]["content"][0]["text"] == f"Hallo {REAL}"


# --- everything else -------------------------------------------------------


def test_an_unrelated_event_passes_through_untouched():
    raw = _run([_event({"type": "response.created", "sequence_number": 0,
                        "response": {"id": "resp_1", "status": "in_progress"}})])
    assert b"response.created" in raw
    assert _payloads(raw)[0]["response"]["id"] == "resp_1"


def test_the_event_line_is_preserved():
    """Clients dispatch on it, so rewriting the payload must not drop it."""
    raw = _run([_event(_delta("hallo"))])
    assert b"event: response.output_text.delta\n" in raw


def test_a_non_json_event_passes_through():
    raw = _run([b": keep-alive\n\n"])
    assert raw == b": keep-alive\n\n"


def test_a_restore_failure_leaves_the_stream_alive():
    async def exploding(text: str) -> str:
        raise RuntimeError("the vault is unavailable")

    raw = _run([
        _event(_delta(f"Hallo {PLACEHOLDER}")),
        _event({"type": "response.output_text.done", "item_id": "msg_1",
                "output_index": 0, "content_index": 0, "sequence_number": 9,
                "text": f"Hallo {PLACEHOLDER}"}),
    ], restore=exploding)

    assert _text(raw) == f"Hallo {PLACEHOLDER}"
