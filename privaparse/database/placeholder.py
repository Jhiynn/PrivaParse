"""Placeholder suffix generation and parsing.

Suffixes come from a single counter shared by **all** entity types, so the
sequence runs ``PERSON_A1``, ``EMAIL_A2``, ``PHONE_A3``. A per-type counter
would produce ``PERSON_A1`` / ``EMAIL_A1`` / ``PHONE_A1``, which reads as if
those three belong to the same person. Phase 1 does no cross-type linking, so
that would be a lie told by the numbering scheme itself.
"""

from __future__ import annotations

import re

_DIGITS_PER_BLOCK = 9

#: Matches a rendered placeholder, e.g. ``[[PERSON_A1]]`` or ``[[EMAIL_AB12]]``.
PLACEHOLDER_RE = re.compile(r"\[\[([A-Z][A-Z0-9]*)_([A-Z]+[0-9]+)\]\]")


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
