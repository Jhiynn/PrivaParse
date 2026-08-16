"""Restoring a Responses API stream.

Not a chat stream with different names. Chat sends anonymous chunks whose
`choices[].delta` is the only place a value appears. The Responses API sends
**typed** events, and repeats every completed value: text arrives as
`response.output_text.delta`, again whole in `response.output_text.done`, and
a third time inside the `response` object of `response.completed`. A client
may read any one of those, so all three are restored.

Two different jobs follow from that:

* a `.delta` carries a fragment, so it needs the hold-back -- the same buffer
  the chat path uses, out of `restore.py`, since that logic is about strings
  and knows nothing about either protocol;
* a `.done` carries the finished value, so it needs no buffering at all, only
  restoration.

Tool-call arguments are the chat rule again for the same reason: fragments of
JSON cannot be parsed and therefore cannot be restored, so they are collected
and emitted once, complete, with the arguments parsed and re-serialised
rather than substituted into.

Both of those rules wait for a `.done` that a stream is not obliged to send.
A dropped connection or a proxy that closes early ends it mid-answer, and the
held text and the collected arguments would then be dropped -- text the
provider was already paid for, and a tool call whose fragments were suppressed
on the way in precisely because a later event was going to carry them. So this
module supplies a flush, and `sse.py` decides when it runs: when the bytes run
out, when the connection drops, at `[DONE]`, and in front of a terminal event.
Past any of those a client has stopped accumulating deltas.

Inserting events means the gateway, not the provider, decides what sequence
the client reads, so `sequence_number` is reissued across the whole stream
rather than passed through.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator
from typing import Any

from privaparse.gateway.adapter.responses import extract_answer
from privaparse.gateway.extract import write_back
from privaparse.gateway.restore import (
    HoldBack,
    Restore,
    guarded_restore,
    restore_arguments,
)
from privaparse.gateway.sse import encode_event, relay_sse

__all__ = ["restore_responses_sse"]

TEXT_DELTA = "response.output_text.delta"
TEXT_DONE = "response.output_text.done"
REFUSAL_DELTA = "response.refusal.delta"
REFUSAL_DONE = "response.refusal.done"
ARGS_DELTA = "response.function_call_arguments.delta"
ARGS_DONE = "response.function_call_arguments.done"
ITEM_DONE = "response.output_item.done"
#: Every terminal event repeats the whole response object.
TERMINAL = frozenset({
    "response.completed", "response.incomplete", "response.failed",
})


def restore_responses_sse(
    chunks: AsyncIterator[bytes],
    *,
    restore: Restore,
    max_hold: int,
    lenient: bool = False,
) -> AsyncIterator[bytes]:
    """Relay a Responses stream, putting real values back as it goes."""
    restored = guarded_restore(restore, lenient=lenient)
    holds: dict[Any, HoldBack] = {}
    arguments: dict[Any, str] = {}
    # The last delta event seen for each of the above, minus its `delta`. A
    # stream that stops without a terminal event has to be answered with an
    # event nobody sent, and these say who it belongs to: the Responses API
    # addresses everything by `item_id` and `content_index`, so an event
    # without them is text the client cannot place.
    text_frames: dict[Any, dict] = {}
    argument_frames: dict[Any, dict] = {}
    next_sequence = 0

    def sequenced(payload: dict) -> dict:
        """Renumber an event, so the sequence the client reads is the one it got.

        `sequence_number` numbers the events of *this* stream, and this module
        emits events the provider never sent -- the held tail, a whole-value
        delta beside each `.done`. Passing the upstream numbers through would
        hand a client two events bearing the same number and a sequence that
        stops being monotonic exactly where the gateway inserted something. So
        the numbering is reissued here: the gateway frames the stream the
        client sees, so it owns the count of it.
        """
        nonlocal next_sequence
        if "sequence_number" in payload:
            payload["sequence_number"] = next_sequence
            next_sequence += 1
        return payload

    def hold_for(payload: dict) -> HoldBack:
        key = _text_key(payload)
        if key not in holds:
            holds[key] = HoldBack(max_hold, lenient=lenient)
        return holds[key]

    async def rewrite(payload: dict) -> list[dict]:
        """Zero or more events to emit in place of this one."""
        kind = payload.get("type")

        if kind in (TEXT_DELTA, REFUSAL_DELTA):
            piece = payload.get("delta")
            if not isinstance(piece, str):
                return [sequenced(payload)]
            text_frames[_text_key(payload)] = _frame(payload)
            payload["delta"] = await restored(hold_for(payload).feed(piece))
            return [sequenced(payload)]

        if kind in (TEXT_DONE, REFUSAL_DONE):
            out: list[dict] = []
            held = hold_for(payload).flush()
            # The hold-back is empty and this text is closed, so nothing is
            # owed on it any more.
            text_frames.pop(_text_key(payload), None)
            if held:
                # Whatever is still held belongs to the deltas, and a client
                # reading only deltas has to receive it before the event that
                # closes the text.
                extra = copy.deepcopy(payload)
                extra["type"] = TEXT_DELTA if kind == TEXT_DONE else REFUSAL_DELTA
                extra["delta"] = await restored(held)
                extra.pop("text", None)
                extra.pop("refusal", None)
                out.append(sequenced(extra))
            for field in ("text", "refusal"):
                if isinstance(payload.get(field), str):
                    payload[field] = await restored(payload[field])
            out.append(sequenced(payload))
            return out

        if kind == ARGS_DELTA:
            piece = payload.get("delta")
            if isinstance(piece, str):
                key = payload.get("item_id")
                arguments[key] = arguments.get(key, "") + piece
                argument_frames[key] = _frame(payload)
            # Suppressed: a fragment of JSON cannot be parsed, so it cannot be
            # restored, and a partial tool call is not executable anyway.
            return []

        if kind == ARGS_DONE:
            key = payload.get("item_id")
            complete = payload.get("arguments")
            if not isinstance(complete, str):
                complete = arguments.get(key, "")
            arguments.pop(key, None)
            argument_frames.pop(key, None)
            fixed = await restore_arguments(complete, restored)
            payload["arguments"] = fixed
            # One delta carrying the whole thing, for a client that accumulates
            # deltas and never reads the done event.
            whole = copy.deepcopy(payload)
            whole["type"] = ARGS_DELTA
            whole["delta"] = fixed
            whole.pop("arguments", None)
            return [sequenced(whole), sequenced(payload)]

        if kind == ITEM_DONE:
            item = payload.get("item")
            if isinstance(item, dict):
                payload["item"] = await _restore_item(item, restored)
            return [sequenced(payload)]

        if kind in TERMINAL:
            answer = payload.get("response")
            if isinstance(answer, dict):
                payload["response"] = await _restore_response(answer, restored)
            return [sequenced(payload)]

        return [sequenced(payload)]

    async def flush() -> AsyncIterator[bytes]:
        """Whatever is still being held when the stream stops, as its own event.

        No `.done` is coming to carry the held text out, and none is coming to
        release the tool call whose fragments were suppressed on the way in.
        Both were paid for.

        Popping the frame is what makes this safe to call more than once, which
        `sse.py` does: a stream reaches it at `response.completed`, again at
        `[DONE]`, and again when the bytes run out.
        """
        for key, hold in holds.items():
            frame = text_frames.pop(key, None)
            left = hold.flush()
            if not left or frame is None:
                continue
            yield _typed(sequenced({**frame, "delta": await restored(left)}))

        for key, collected in arguments.items():
            frame = argument_frames.pop(key, None)
            if not collected or frame is None:
                continue
            # A delta, not a synthesised `.done`: the arguments may have
            # stopped mid-JSON, and an event named for completion would claim
            # they did not. `restore_arguments` restores either way.
            yield _typed(sequenced({
                **frame,
                "type": ARGS_DELTA,
                "delta": await restore_arguments(collected, restored),
            }))

    def ends_the_answer(payload: dict) -> bool:
        """A terminal event is the last word, so the flush goes in front of it.

        Anything still held belongs to the deltas that came before it. Behind
        it, it would arrive after the event that says there is no more, out of
        sequence and past the point a client stops accumulating.
        """
        return payload.get("type") in TERMINAL

    return relay_sse(
        chunks, rewrite=rewrite, flush=flush, ends_the_answer=ends_the_answer
    )


def _text_key(payload: dict) -> tuple:
    """Which run of text an event belongs to. One hold-back per run."""
    return (payload.get("item_id"), payload.get("content_index"))


def _frame(payload: dict) -> dict:
    """The event without its fragment -- enough to address a later one here."""
    return {key: value for key, value in payload.items() if key != "delta"}


def _typed(payload: dict[str, Any]) -> bytes:
    """An event the gateway invented, framed with its `event:` line as well.

    Clients dispatch on that line, and nothing else in the stream carries what
    these events hold: one invisible to such a client would lose exactly what
    it was emitted to save.
    """
    return encode_event(payload, name=payload["type"])


async def _restore_item(item: dict, restored: Restore) -> dict:
    """One output item, restored in place: message content or tool arguments."""
    out = copy.deepcopy(item)
    content = out.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                part["text"] = await restored(part["text"])
    if isinstance(out.get("arguments"), str):
        out["arguments"] = await restore_arguments(out["arguments"], restored)
    return out


async def _restore_response(answer: dict, restored: Restore) -> dict:
    """The whole response object, through the ordinary response walk."""
    nodes = extract_answer(answer)
    if not nodes:
        return answer
    values = [await restored(node.text) for node in nodes]
    return write_back(answer, nodes, values)
