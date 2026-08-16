"""The SSE framing loop, once, for every protocol that streams over it.

Server-sent events are the same wire format whoever is speaking them: bytes
arrive chopped wherever the transport felt like chopping them, events are
separated by a blank line, a `data:` line carries the payload, several of them
carry one payload between them, and `[DONE]` ends the stream. None of that is
per-protocol, and it was written twice before this module existed -- with the
end-of-stream flush on one side only, which is the data loss #8 reported.

What *is* per-protocol is what to do with a parsed event, and that arrives as
:class:`EventRewrite`. The protocol never sees a byte, a blank line or the
`data:` prefix; it is handed a payload and returns the payloads to send in its
place -- none, one, or several.

The flush is the reason this module exists rather than a helper or two. A
protocol that holds text back -- and every protocol restoring placeholders in a
stream has to -- owes the caller whatever it is sitting on the moment the
stream stops, and a stream stops in four ways: the bytes run out, the
connection raises, `[DONE]` arrives, or the protocol's own terminal event
arrives. Each of those is a place a protocol could forget. Here they are one
list, in one loop: a protocol says *what* it is holding and never *when* to
hand it over, and :func:`relay_sse` will not run without being told what.

That guarantee reaches as far as SSE does and no further. A protocol that
frames its stream some other way supplies its own relay -- the adapter's slot
takes the whole thing on purpose (ADR-0003) -- and for that one the flush is
guaranteed by the conformance test rather than by this module.

Nothing here aborts an answer. An event that is not JSON, is not an object, or
carries no `data:` line at all is relayed byte for byte: whatever it is, the
caller has already paid for it, and this module is not the last reader of the
stream.
"""

from __future__ import annotations

import codecs
import json
from collections.abc import AsyncIterator, Iterable
from typing import Any, Protocol

__all__ = [
    "EndsTheAnswer",
    "EventRewrite",
    "Flush",
    "encode_event",
    "relay_sse",
]

_DONE = "[DONE]"
_DATA_PREFIX = "data:"


class EventRewrite(Protocol):
    """One parsed event in, the events to send in its place out.

    A list rather than a single payload because both directions matter: a
    protocol suppresses an event by returning none of them -- a fragment of a
    tool call cannot be restored, so it is collected instead of relayed -- and
    adds one by returning two, which is how a whole value reaches a client that
    reads only deltas.

    The first payload keeps the framing of the event it replaces, `event:` line
    included; the rest are framed as plain `data:` events.
    """

    async def __call__(self, payload: dict[str, Any]) -> list[dict[str, Any]]: ...


class Flush(Protocol):
    """Whatever the rewrite is still holding, as events, ready to send.

    Called at every point the stream can stop, so it has to be safe to call
    more than once and yield nothing the second time: one stream reaches it at
    a terminal event, again at `[DONE]`, and again when the bytes run out.

    It yields bytes rather than payloads because these events are the
    gateway's own invention rather than a rewrite of somebody else's, and how
    one is framed -- whether a client dispatching on an `event:` line can see
    it -- is the protocol's business. :func:`encode_event` frames them.
    """

    def __call__(self) -> AsyncIterator[bytes]: ...


class EndsTheAnswer(Protocol):
    """Whether this event is the last word, so the flush goes in front of it.

    Past such an event a client has stopped accumulating, so anything held
    back has to arrive before it rather than after. A protocol whose stream
    ends only at `[DONE]` does not supply one.
    """

    def __call__(self, payload: dict[str, Any]) -> bool: ...


async def relay_sse(
    chunks: AsyncIterator[bytes],
    *,
    rewrite: EventRewrite,
    flush: Flush,
    ends_the_answer: EndsTheAnswer | None = None,
) -> AsyncIterator[bytes]:
    """Relay an SSE stream, rewriting the events one protocol understands."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    pending = ""

    try:
        async for raw in chunks:
            # CRLF framing is normalised away rather than handled twice. A
            # literal newline inside a JSON string is escaped, so nothing in
            # the payload can be touched by this.
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
                    yield _verbatim(block)
                    continue

                joined = "\n".join(data)
                if joined.strip() == _DONE:
                    # A client is entitled to stop reading here, so anything
                    # still held goes out in front of the sentinel.
                    async for event in flush():
                        yield event
                    yield _verbatim(block)
                    continue

                try:
                    payload = json.loads(joined)
                except ValueError:
                    yield _verbatim(block)
                    continue
                if not isinstance(payload, dict):
                    yield _verbatim(block)
                    continue

                if ends_the_answer is not None and ends_the_answer(payload):
                    async for event in flush():
                        yield event

                for index, out in enumerate(await rewrite(payload)):
                    if index == 0:
                        # Keep the original framing, `event:` line included --
                        # clients dispatch on it.
                        yield _reassemble(lines, out)
                    else:
                        yield encode_event(out)
    except Exception:
        # The stop this module exists for. A provider connection that dies
        # mid-answer surfaces as an exception rather than as bytes that run
        # out, and what is held is no less paid for on this path than on the
        # clean one. Hand it over, then let the error travel: swallowing it
        # would report a truncated answer as a whole one.
        async for event in flush():
            yield event
        raise

    pending += decoder.decode(b"", True)
    if pending.strip():
        # A final event with no terminator. Unparseable as it stands, and the
        # caller is owed the bytes.
        yield pending.encode("utf-8")
    async for event in flush():
        yield event


def encode_event(payload: dict[str, Any], *, name: str | None = None) -> bytes:
    """One payload as one event.

    `name` writes the `event:` line. An event the gateway invented needs it
    when the protocol addresses its events by type: clients dispatch on that
    line, and an inserted event invisible to such a client would lose exactly
    what it was emitted to save.
    """
    body = json.dumps(payload, ensure_ascii=False)
    prefix = f"event: {name}\n" if name is not None else ""
    return f"{prefix}data: {body}\n\n".encode()


def _verbatim(block: str) -> bytes:
    """An event relayed as it arrived, terminator restored."""
    return (block + "\n\n").encode("utf-8")


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
