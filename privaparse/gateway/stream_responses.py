"""Restoring a Responses API stream.

Not a chat stream with different names. Chat sends anonymous chunks whose
`choices[].delta` is the only place a value appears. The Responses API sends
**typed** events, and repeats every completed value: text arrives as
`response.output_text.delta`, again whole in `response.output_text.done`, and
a third time inside the `response` object of `response.completed`. A client
may read any one of those, so all three are restored.

Two different jobs follow from that:

* a `.delta` carries a fragment, so it needs the hold-back -- the same buffer
  the chat path uses, since that logic is about strings and knows nothing
  about either protocol;
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
on the way in precisely because a later event was going to carry them. So the
stream is answered when its bytes run out, in events this module frames itself.

The SSE framing loop below is close to the one in `stream.py`. It is repeated
rather than shared: the chat path is the tested one that ships today, and a
refactor that reached into it to serve a second protocol would put it at risk
for twenty lines.
"""

from __future__ import annotations

import codecs
import copy
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from privaparse.app.logging import get_logger
from privaparse.database.placeholder import contains_placeholder
from privaparse.gateway.adapter.responses import extract_output
from privaparse.gateway.extract import write_back
from privaparse.gateway.stream import (
    _MAYBE_MANGLED_RE,
    HoldBack,
    _encode,
    _raw,
    _reassemble,
    _restore_arguments,
)

logger = get_logger(__name__)

__all__ = ["restore_responses_sse"]

_DATA_PREFIX = "data:"
_DONE = "[DONE]"

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


async def restore_responses_sse(
    chunks: AsyncIterator[bytes],
    *,
    restore: Callable[[str], Awaitable[str]],
    max_hold: int,
    lenient: bool = False,
) -> AsyncIterator[bytes]:
    """Relay a Responses stream, putting real values back as it goes."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    holds: dict[Any, HoldBack] = {}
    arguments: dict[Any, str] = {}
    # The last delta event seen for each of the above, minus its `delta`. A
    # stream that stops without a terminal event has to be answered with an
    # event nobody sent, and these say who it belongs to: the Responses API
    # addresses everything by `item_id` and `content_index`, so an event
    # without them is text the client cannot place.
    frames: dict[Any, dict] = {}
    argument_frames: dict[Any, dict] = {}
    pending = ""

    async def restored(text: str) -> str:
        if not text:
            return text
        worth_asking = contains_placeholder(text) or (
            lenient and _MAYBE_MANGLED_RE.search(text) is not None
        )
        if not worth_asking:
            return text
        try:
            return await restore(text)
        except Exception:  # noqa: BLE001 - a streamed answer is never aborted
            logger.warning("could not restore a streamed answer; placeholders stand")
            return text

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
                return [payload]
            frames[_text_key(payload)] = _frame(payload)
            payload["delta"] = await restored(hold_for(payload).feed(piece))
            return [payload]

        if kind in (TEXT_DONE, REFUSAL_DONE):
            out: list[dict] = []
            held = hold_for(payload).flush()
            # The hold-back is empty and this text is closed, so nothing is
            # owed on it any more.
            frames.pop(_text_key(payload), None)
            if held:
                # Whatever is still held belongs to the deltas, and a client
                # reading only deltas has to receive it before the event that
                # closes the text.
                extra = copy.deepcopy(payload)
                extra["type"] = TEXT_DELTA if kind == TEXT_DONE else REFUSAL_DELTA
                extra["delta"] = await restored(held)
                extra.pop("text", None)
                extra.pop("refusal", None)
                out.append(extra)
            for field in ("text", "refusal"):
                if isinstance(payload.get(field), str):
                    payload[field] = await restored(payload[field])
            out.append(payload)
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
            fixed = await _restore_arguments(complete, restored)
            payload["arguments"] = fixed
            # One delta carrying the whole thing, for a client that accumulates
            # deltas and never reads the done event.
            whole = copy.deepcopy(payload)
            whole["type"] = ARGS_DELTA
            whole["delta"] = fixed
            whole.pop("arguments", None)
            return [whole, payload]

        if kind == ITEM_DONE:
            item = payload.get("item")
            if isinstance(item, dict):
                payload["item"] = await _restore_item(item, restored)
            return [payload]

        if kind in TERMINAL:
            answer = payload.get("response")
            if isinstance(answer, dict):
                payload["response"] = await _restore_response(answer, restored)
            return [payload]

        return [payload]

    async def tail() -> AsyncIterator[bytes]:
        """Whatever is still being held when the stream stops, as its own event.

        Reached when a provider ends without a terminal event -- a dropped
        connection, a proxy that closes early. No `.done` is coming to carry
        the held text out, and no `.done` is coming to release the tool call
        whose fragments were suppressed on the way in. Both were paid for.

        Popping the frame is what makes this safe to call twice: a stream that
        ends with `[DONE]` reaches it there and again when the bytes run out.
        """
        for key, hold in holds.items():
            frame = frames.pop(key, None)
            left = hold.flush()
            if not left or frame is None:
                continue
            yield _typed({**frame, "delta": await restored(left)})

        for key, collected in arguments.items():
            frame = argument_frames.pop(key, None)
            if not collected or frame is None:
                continue
            # A delta, not a synthesised `.done`: the arguments may have
            # stopped mid-JSON, and an event named for completion would claim
            # they did not. `_restore_arguments` restores either way.
            yield _typed({
                **frame,
                "type": ARGS_DELTA,
                "delta": await _restore_arguments(collected, restored),
            })

    async for raw in chunks:
        pending += decoder.decode(raw).replace("\r\n", "\n")
        while "\n\n" in pending:
            block, pending = pending.split("\n\n", 1)
            lines = block.split("\n")
            data = [
                line[len(_DATA_PREFIX):].lstrip()
                for line in lines
                if line.startswith(_DATA_PREFIX)
            ]
            if not data:
                yield _raw(block)
                continue

            joined = "\n".join(data)
            if joined.strip() == _DONE:
                # A client is entitled to stop reading here, so anything still
                # held has to go out in front of the sentinel.
                async for event in tail():
                    yield event
                yield _raw(block)
                continue

            try:
                payload = json.loads(joined)
            except ValueError:
                yield _raw(block)
                continue
            if not isinstance(payload, dict):
                yield _raw(block)
                continue

            for index, out in enumerate(await rewrite(payload)):
                if index == 0:
                    # Keep the original framing, `event:` line included --
                    # clients dispatch on it.
                    yield _reassemble(lines, out)
                else:
                    yield _encode(out)

    pending += decoder.decode(b"", True)
    if pending.strip():
        yield pending.encode("utf-8")
    async for event in tail():
        yield event


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
    body = json.dumps(payload, ensure_ascii=False)
    return f"event: {payload['type']}\ndata: {body}\n\n".encode()


async def _restore_item(item: dict, restored) -> dict:
    """One output item, restored in place: message content or tool arguments."""
    out = copy.deepcopy(item)
    content = out.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                part["text"] = await restored(part["text"])
    if isinstance(out.get("arguments"), str):
        out["arguments"] = await _restore_arguments(out["arguments"], restored)
    return out


async def _restore_response(answer: dict, restored) -> dict:
    """The whole response object, through the ordinary response walk."""
    nodes = extract_output(answer)
    if not nodes:
        return answer
    values = [await restored(node.text) for node in nodes]
    return write_back(answer, nodes, values)
