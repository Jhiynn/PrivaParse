"""Placeholder suffix generation and parsing.

Suffixes come from a single counter shared by **all** placeholder types, so the
sequence runs ``PERSON_A1``, ``EMAIL_A2``, ``PHONE_A3``. A per-type counter
would produce ``PERSON_A1`` / ``EMAIL_A1`` / ``PHONE_A1``, which reads as if
those three belong to the same person. Phase 1 does no cross-type linking, so
that would be a lie told by the numbering scheme itself.
"""

from __future__ import annotations

import re

_DIGITS_PER_BLOCK = 9

#: The two grammar fragments shared below, so a type name's rules and a
#: placeholder's rules cannot drift into disagreement with each other. A type
#: name may itself contain underscores (``NATIONAL_ID``, so it reads in a
#: prompt the way a human would write it) because the suffix grammar makes
#: the split unambiguous regardless: ``_SUFFIX_GROUP`` never contains an
#: underscore, so the *last* underscore in a rendered placeholder is always
#: the real separator, and a greedy ``TYPE_NAME_GROUP`` backtracks onto
#: exactly that one. See ``PLACEHOLDER_RE`` below for the worked case.
_TYPE_NAME_GROUP = r"[A-Z][A-Z0-9_]*"
_SUFFIX_GROUP = r"[A-Z]+[0-9]+"

#: A type name alone, anchored — what a catalogue entry's key must satisfy to
#: ever appear as the left half of a rendered placeholder. Built from the same
#: fragment as ``PLACEHOLDER_RE`` rather than writing the character class a
#: second time, so the two cannot silently disagree after an edit.
TYPE_NAME_RE = re.compile(rf"^{_TYPE_NAME_GROUP}$")

#: Matches a rendered placeholder, e.g. ``[[PERSON_A1]]`` or
#: ``[[NATIONAL_ID_A1]]``. Unambiguous even though the type group now allows
#: underscores: ``re`` backtracks a greedy quantifier from the longest match
#: down, so for ``NATIONAL_ID_A1`` it first tries type="NATIONAL_ID_A1" (no
#: literal "_" left to match), then shrinks one character at a time until it
#: reaches the last underscore in the string — type="NATIONAL_ID",
#: suffix="A1" — which is also the *first* position where the rest of the
#: pattern succeeds, so backtracking stops there. That is always the real
#: separator ``format_placeholder`` inserted, because ``_SUFFIX_GROUP`` itself
#: can never contain an underscore, so nothing appended after it could ever
#: introduce a later one.
PLACEHOLDER_RE = re.compile(rf"\[\[({_TYPE_NAME_GROUP})_({_SUFFIX_GROUP})\]\]")


def encode_suffix(index: int) -> str:
    """Map a 0-based counter onto ``A1..A9, B1..B9, ..., Z9, AA1, ...``.

    >>> encode_suffix(0), encode_suffix(8), encode_suffix(9), encode_suffix(233)
    ('A1', 'A9', 'B1', 'Z9')
    >>> encode_suffix(234)
    'AA1'
    """
    if index < 0:
        raise ValueError(f"suffix index must be non-negative, got {index}")
    block, digit = divmod(index, _DIGITS_PER_BLOCK)
    return f"{_letters(block)}{digit + 1}"


def _letters(block: int) -> str:
    """Bijective base-26: 0 -> A, 25 -> Z, 26 -> AA, 27 -> AB."""
    out: list[str] = []
    n = block + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out.append(chr(ord("A") + rem))
    return "".join(reversed(out))


def format_placeholder(entity_type: str, suffix: str) -> str:
    return f"[[{entity_type}_{suffix}]]"


def build_placeholder(entity_type: str, index: int) -> str:
    return format_placeholder(entity_type, encode_suffix(index))


def find_placeholders(text: str) -> list[re.Match[str]]:
    """All placeholder occurrences in ``text``, in order."""
    return list(PLACEHOLDER_RE.finditer(text))


def contains_placeholder(text: str) -> bool:
    return PLACEHOLDER_RE.search(text) is not None
