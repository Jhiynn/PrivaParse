"""Putting real values back into an answer that arrives in pieces.

A streamed placeholder is not delivered whole. `[[PERSON_A1]]` reaches the
gateway as `[[PER`, `SON_`, `A1]]`, or as thirteen separate events, and a
restorer looking at one piece at a time finds nothing to restore in any of
them. So the tail of the text is held back until it either completes into a
placeholder or proves it cannot become one.

Tool-call arguments are held back for a different reason and for longer. They
arrive as fragments of JSON -- `{"to": "`, then `[[EMAIL`, then `_A2]]"}` --
which cannot be parsed, and therefore cannot be restored, until the last one
lands. So they are collected and restored once, whole.

Nothing here knows about HTTP, SSE, or which protocol is being spoken: this is
the reasoning about strings, and it is testable with plain strings. Both
protocol relays hold it in common -- one of them used to reach into the other
for it, through names that were never public.

Like the non-streaming response path, nothing here aborts an answer. A
restoration that fails shows a placeholder; an exception would truncate an
answer the caller has already paid for.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from privaparse.app.logging import get_logger
from privaparse.database.placeholder import contains_placeholder

logger = get_logger(__name__)

__all__ = [
    "HoldBack",
    "Restore",
    "guarded_restore",
    "max_placeholder_length",
    "restore_arguments",
]

#: A vault lookup for one mapping: text in, text with real values out.
Restore = Callable[[str], Awaitable[str]]

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


def guarded_restore(restore: Restore, *, lenient: bool = False) -> Restore:
    """A vault lookup wrapped in the two rules every streamed answer needs.

    Asked only when there is something to look up -- a token-by-token stream
    would otherwise hit the database once per token to restore text that
    plainly holds no placeholder -- and never allowed to raise.
    """

    async def restored(text: str) -> str:
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

    return restored


async def restore_arguments(raw: str, restored: Restore) -> str:
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


async def _restore_leaves(value: Any, restored: Restore) -> Any:
    if isinstance(value, str):
        return await restored(value)
    if isinstance(value, dict):
        return {key: await _restore_leaves(sub, restored) for key, sub in value.items()}
    if isinstance(value, list):
        return [await _restore_leaves(sub, restored) for sub in value]
    return value
