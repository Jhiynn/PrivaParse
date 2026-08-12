"""Restoring an answer that arrives in pieces.

A streamed placeholder is not delivered whole. `[[PERSON_A1]]` reaches the
gateway as `[[PER`, `SON_`, `A1]]`, or as thirteen separate events, and a
restorer looking at one piece at a time finds nothing to restore in any of
them. So the tail of the text is held back until it either completes into a
placeholder or proves it cannot become one.

Two layers, deliberately apart. :class:`HoldBack` is the buffering rule and
knows nothing about HTTP; :func:`restore_sse` frames the events and calls the
vault. The first is where the hard reasoning lives and it is testable with
plain strings.

Tool calls are held back for a different reason and for longer. Their
arguments arrive as JSON fragments -- `{"to": "`, then `[[EMAIL`, then
`_A2]]"}` -- which cannot be parsed, and therefore cannot be restored, until
the last one lands. So the fragments are collected and the call is emitted
once, complete, on the chunk that finishes the choice. Nothing is lost by not
streaming them: a partial tool call is not executable.

Like the non-streaming response path, nothing here aborts an answer. A
restoration that fails shows a placeholder; an exception would truncate an
answer the caller has already paid for.
"""

from __future__ import annotations

import codecs
import json
import re
from typing import Any, AsyncIterator, Awaitable, Callable, Iterable

from privaparse.app.logging import get_logger
from privaparse.database.placeholder import contains_placeholder

logger = get_logger(__name__)

__all__ = ["HoldBack", "max_placeholder_length", "restore_sse"]

_DONE = "[DONE]"
_DATA_PREFIX = "data:"

#: Any *prefix* of a rendered placeholder, anchored to the end of the buffer:
#: `[`, `[[`, `[[PER`, `[[PERSON_A1`, `[[PERSON_A1]`. A complete
#: `[[PERSON_A1]]` deliberately does not match -- it is finished, so there is
#: nothing left to wait for. Grammar rather than length is what decides: text
#: like `see [[this]]` fails the very first character after `[[` and is
#: released without waiting for anything.
_PARTIAL_TAIL_RE = re.compile(r"\[(?:\[(?:[A-Z][A-Z0-9_]*\]?)?)?$")

#: The same idea with one bracket pair made optional, for when the tolerant
#: matcher is on. A model that drops a bracket emits `[PERSON_A1]`, which the
#: strict pattern releases immediately and therefore hands over split across
#: events -- unrestorable however tolerant the matcher downstream is.
_LENIENT_TAIL_RE = re.compile(r"\[\[?(?:[A-Z][A-Z0-9_]*\]?)?$")

#: Worth asking the vault about, when the tolerant matcher is on. A bracket of
#: any kind, or a bare `Something_A1` / `Something A1` token -- the shapes a
#: mangled placeholder can take. Deliberately broad: it only decides whether a
#: lookup happens, never what may be substituted, and that decision is still
#: made against the placeholders this one mapping issued.
_MAYBE_MANGLED_RE = re.compile(r"\[|(?<!\w)[A-Za-z][A-Za-z0-9_]*[_\s][A-Za-z]+[0-9]+(?!\w)")

#: Room for the suffix in :func:`max_placeholder_length`. Suffixes run
#: ``A1..Z9, AA1, ...``, so eight characters covers more entities in one
#: mapping than a single request could ever produce.
_SUFFIX_ALLOWANCE = 8


def max_placeholder_length(catalogue: Any) -> int:
    """The longest placeholder this catalogue could render, plus suffix room.

    The hold-back needs a ceiling as well as a grammar. Text like
    ``[[AAAAAA...`` stays grammatical for as long as the capitals keep coming,
    and without a cap a model emitting one would stall the stream for as long
    as it kept going.
    """
    names = [placeholder.name for placeholder in catalogue.enabled]
    longest = max((len(name) for name in names), default=0)
    return len("[[") + longest + len("_") + _SUFFIX_ALLOWANCE + len("]]")


class HoldBack:
    """Releases text up to the last point that could still start a placeholder.

    Not a parser: it never decides what a placeholder *means*, only where it is
    unsafe to cut. Everything before that point is handed on immediately, so a
    stream of ordinary prose flows through untouched.
    """

    def __init__(self, max_hold: int, lenient: bool = False) -> None:
        self.max_hold = max_hold
        self._pattern = _LENIENT_TAIL_RE if lenient else _PARTIAL_TAIL_RE
        self._held = ""

    def feed(self, text: str) -> str:
        """Add `text`; return everything now safe to emit."""
        buffer = self._held + text
        match = self._pattern.search(buffer)
        if match is None:
            self._held = ""
            return buffer

        start = match.start()
        if len(buffer) - start > self.max_hold:
            # Too long to be a placeholder, whatever it looks like. Holding it
            # any longer would trade a leak-free stream for a stalled one.
            self._held = ""
            return buffer

        self._held = buffer[start:]
        return buffer[:start]

    def flush(self) -> str:
        """Release the tail unconditionally. The stream is over.

        What comes back may be half a placeholder. It is emitted anyway: the
        provider was paid for those characters, and dropping them would edit
        the answer rather than fail to improve it.
        """
        held, self._held = self._held, ""
        return held


async def restore_sse(
    chunks: AsyncIterator[bytes],
    *,
    restore: Callable[[str], Awaitable[str]],
    max_hold: int,
    lenient: bool = False,
) -> AsyncIterator[bytes]:
    """Relay an SSE completion stream, putting real values back as it goes.

    Everything the walk does not recognise -- a comment, an event that is not
    JSON, a chunk carrying only usage -- is passed through byte for byte.
    """
    decoder = codecs.getincrementaldecoder("utf-8")()
    holds: dict[Any, HoldBack] = {}
    # choice index -> tool-call index -> the call being assembled. Arguments
    # arrive as JSON fragments that cannot be parsed until the last one lands,
    # so they are collected rather than relayed.
    calls: dict[Any, dict[Any, dict[str, Any]]] = {}
    skeleton: dict[str, Any] | None = None
    pending = ""

    async def restored(text: str) -> str:
        # The vault is only consulted when there is something to look up. A
        # token-by-token stream would otherwise hit the database once per
        # token to restore text that plainly holds no placeholder.
        #
        # The test for "something to look up" has to widen with the matcher:
        # `contains_placeholder` wants `[[...]]`, so on its own it would skip
        # exactly the mangled forms the tolerant matcher exists to catch.
        worth_asking = contains_placeholder(text) or (
            lenient and _MAYBE_MANGLED_RE.search(text) is not None
        )
        if not text or not worth_asking:
            return text
        try:
            return await restore(text)
        except Exception:  # noqa: BLE001 - a streamed answer is never aborted
            # No payload in the message: what failed to restore is by
            # definition the part of the answer that concerns a person.
            logger.warning("could not restore a streamed answer; placeholders stand")
            return text

    async def rewrite(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal skeleton
        choices = payload.get("choices")
        if not isinstance(choices, list):
            return payload

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
                # be completed by a later delta, so it goes out here.
                released += hold.flush()
                buffered = calls.pop(index, None)
                if buffered:
                    delta["tool_calls"] = await _assemble(buffered, restored)
            if isinstance(content, str) or released:
                delta["content"] = await restored(released)
        return payload

    async def tail() -> AsyncIterator[bytes]:
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
            yield _encode(payload)

    async for raw in chunks:
        # CRLF framing is normalised away rather than handled twice. A literal
        # newline inside a JSON string is escaped, so nothing in the payload
        # can be touched by this.
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

            yield _reassemble(lines, await rewrite(payload))

    pending += decoder.decode(b"", True)
    if pending.strip():
        # A final event with no terminator. Unparseable as it stands, and the
        # caller is owed the bytes.
        yield pending.encode("utf-8")
    async for event in tail():
        yield event


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
    buffered: dict[Any, dict[str, Any]], restored: Callable[[str], Awaitable[str]]
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
        function["arguments"] = await _restore_arguments(slot["arguments"], restored)
        entry["function"] = function
        out.append(entry)
    return out


async def _restore_arguments(raw: str, restored: Callable[[str], Awaitable[str]]) -> str:
    """Parse, restore each string leaf, re-serialise.

    Not a string substitution over the serialised form: a restored value
    holding a quote or a backslash would produce arguments the client cannot
    parse, and it is about to execute them. Keys are left alone -- they are
    parameter names from the client's own schema, the same rule the
    non-streaming walk follows.
    """
    if not raw:
        return raw
    try:
        parsed = json.loads(raw)
    except ValueError:
        # Never completed, so there is nothing to re-serialise. The caller
        # gets a broken tool call either way; it should not also get one with
        # a placeholder in it.
        return await restored(raw)
    return json.dumps(await _restore_leaves(parsed, restored), ensure_ascii=False)


async def _restore_leaves(value: Any, restored: Callable[[str], Awaitable[str]]) -> Any:
    if isinstance(value, str):
        return await restored(value)
    if isinstance(value, dict):
        return {key: await _restore_leaves(sub, restored) for key, sub in value.items()}
    if isinstance(value, list):
        return [await _restore_leaves(sub, restored) for sub in value]
    return value


def _raw(block: str) -> bytes:
    return (block + "\n\n").encode("utf-8")


def _encode(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def _reassemble(lines: Iterable[str], payload: dict[str, Any]) -> bytes:
    """Put the rewritten payload back into the event it came from.

    Non-data lines are kept in place. Several `data:` lines are one payload
    per the SSE spec, so they collapse into the single line that replaces them.
    """
    encoded = json.dumps(payload, ensure_ascii=False)
    out: list[str] = []
    written = False
    for line in lines:
        if not line.startswith(_DATA_PREFIX):
            out.append(line)
            continue
        if written:
            continue
        out.append(f"data: {encoded}")
        written = True
    return ("\n".join(out) + "\n\n").encode("utf-8")
