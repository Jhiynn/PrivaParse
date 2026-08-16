"""Restoring a Chat Completions stream: what one streamed chunk means.

The framing this rides on is in `sse.py` and the hold-back is in `restore.py`;
neither knows this protocol and this module knows neither SSE nor strings. What
is left here is the one thing that is Chat Completions and nothing else: a
chunk carries `choices[].delta`, text sits at `delta.content`, tool calls
arrive as fragments under `delta.tool_calls`, and `finish_reason` is the last
word on a choice.

Tool calls are held back for longer than text and for a different reason. Their
arguments arrive as JSON fragments -- `{"to": "`, then `[[EMAIL`, then
`_A2]]"}` -- which cannot be parsed, and therefore cannot be restored, until
the last one lands. So the fragments are collected and the call is emitted
once, complete, on the chunk that finishes the choice. Nothing is lost by not
streaming them: a partial tool call is not executable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from privaparse.gateway.restore import (
    HoldBack,
    Restore,
    guarded_restore,
    restore_arguments,
)
from privaparse.gateway.sse import encode_event, relay_sse

__all__ = ["restore_sse"]


def restore_sse(
    chunks: AsyncIterator[bytes],
    *,
    restore: Restore,
    max_hold: int,
    lenient: bool = False,
) -> AsyncIterator[bytes]:
    """Relay an SSE completion stream, putting real values back as it goes.

    Everything the walk does not recognise -- a comment, an event that is not
    JSON, a chunk carrying only usage -- is passed through byte for byte.
    """
    restored = guarded_restore(restore, lenient=lenient)
    holds: dict[Any, HoldBack] = {}
    # choice index -> tool-call index -> the call being assembled. Arguments
    # arrive as JSON fragments that cannot be parsed until the last one lands,
    # so they are collected rather than relayed.
    calls: dict[Any, dict[Any, dict[str, Any]]] = {}
    # The last chunk seen, minus its choices. A flush has to be framed as a
    # chunk of the same completion, and this is what says which one.
    skeleton: dict[str, Any] | None = None

    async def rewrite(payload: dict[str, Any]) -> list[dict[str, Any]]:
        nonlocal skeleton
        choices = payload.get("choices")
        if not isinstance(choices, list):
            return [payload]

        skeleton = {key: value for key, value in payload.items() if key != "choices"}
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            index = choice.get("index", 0)
            hold = holds.setdefault(index, HoldBack(max_hold, lenient=lenient))
            content = delta.get("content")
            released = hold.feed(content) if isinstance(content, str) else ""

            _collect_tool_calls(delta.pop("tool_calls", None), calls.setdefault(index, {}))

            if choice.get("finish_reason") is not None:
                # Last word on this choice. Whatever is still held will never
                # be completed by a later delta, so it goes out here -- a
                # client is entitled to stop reading a choice at this point.
                released += hold.flush()
                buffered = calls.pop(index, None)
                if buffered:
                    delta["tool_calls"] = await _assemble(buffered, restored)
            if isinstance(content, str) or released:
                delta["content"] = await restored(released)
        return [payload]

    async def flush() -> AsyncIterator[bytes]:
        """Anything still held when the stream stops, as its own chunk.

        Reached when a provider ends without a `finish_reason` -- a dropped
        connection, a proxy that closes early. The held text and any tool call
        assembled so far are still the caller's.
        """
        for index in list(dict.fromkeys([*holds, *calls])):
            left = holds[index].flush() if index in holds else ""
            buffered = calls.pop(index, None)
            if skeleton is None or (not left and not buffered):
                continue
            delta: dict[str, Any] = {}
            if left:
                delta["content"] = await restored(left)
            if buffered:
                delta["tool_calls"] = await _assemble(buffered, restored)
            payload = dict(skeleton)
            payload["choices"] = [{"index": index, "delta": delta, "finish_reason": None}]
            yield encode_event(payload)

    return relay_sse(chunks, rewrite=rewrite, flush=flush)


def _collect_tool_calls(fragments: Any, buffered: dict[Any, dict[str, Any]]) -> None:
    """Merge a delta's tool-call fragments into what is being assembled.

    A provider sends the id, the type and the function name with the first
    fragment of each call and only argument text after that. The name is
    appended rather than assigned because a provider that splits it would
    otherwise lose everything but the last piece.
    """
    if not isinstance(fragments, list):
        return

    for fragment in fragments:
        if not isinstance(fragment, dict):
            continue
        slot = buffered.setdefault(fragment.get("index", 0), {"arguments": ""})
        for key in ("id", "type"):
            if fragment.get(key) is not None:
                slot[key] = fragment[key]
        function = fragment.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if isinstance(name, str):
            slot["name"] = slot.get("name", "") + name
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            slot["arguments"] += arguments


async def _assemble(
    buffered: dict[Any, dict[str, Any]], restored: Restore
) -> list[dict[str, Any]]:
    """One complete tool call per index, arguments restored."""
    out: list[dict[str, Any]] = []
    for index in sorted(buffered, key=lambda value: (isinstance(value, str), value)):
        slot = buffered[index]
        entry: dict[str, Any] = {"index": index}
        if "id" in slot:
            entry["id"] = slot["id"]
        entry["type"] = slot.get("type", "function")
        function: dict[str, Any] = {}
        if "name" in slot:
            function["name"] = slot["name"]
        function["arguments"] = await restore_arguments(slot["arguments"], restored)
        entry["function"] = function
        out.append(entry)
    return out
