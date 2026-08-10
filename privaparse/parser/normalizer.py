"""Normalisation — the function that decides what counts as "the same value".

The vault is keyed by ``(type, normalized_value)``, so this module alone
determines whether ``Dr. Max Mustermann`` and ``MAX MUSTERMANN`` end up sharing
a placeholder. Normalising too little fragments one person across several
placeholders; too much merges two different people into one. The surface form is
always kept separately, so normalisation never damages what gets restored.
"""

from __future__ import annotations

import re
import unicodedata

import phonenumbers

from privaparse.app.logging import get_logger
from privaparse.parser.registry import register_normalizer
from privaparse.parser.types import EntityType

log = get_logger("normalizer")

__all__ = [
    "normalize",
    "normalize_person",
    "normalize_email",
    "normalize_phone",
    "normalize_casefold",
]

_WHITESPACE_RE = re.compile(r"\s+")
_NON_PHONE_RE = re.compile(r"[^\d+]")

# Stripped from the front of a name so that "Dr. Max Mustermann" and
# "Max Mustermann" resolve to one person.
#
# Nobility particles (von, van, zu, de, della) are deliberately NOT here: in
# German they are part of the legal name, and dropping them would merge
# "Max von Bergen" with an unrelated "Max Bergen".
_TITLES = frozenset(
    {
        "herr",
        "herrn",
        "frau",
        "hr",
        "fr",
        "dr",
        "prof",
        "professor",
        "dipl",
        "ing",
        "mag",
        "med",
        "rer",
        "nat",
        "pol",
        "phil",
        "oec",
        "hc",
        "ra",
        "rae",
        "rechtsanwalt",
        "rechtsanwaeltin",
        "rechtsanwältin",
        "notar",
        "notarin",
        "msc",
        "bsc",
        "mba",
        "llm",
    }
)


def normalize(value: str, entity_type: str) -> str:
    """Map a surface form onto its vault key."""
    if entity_type == EntityType.EMAIL:
        return normalize_email(value)
    if entity_type == EntityType.PHONE:
        return normalize_phone(value)
    return normalize_person(value)


@register_normalizer("email")
def normalize_email(value: str) -> str:
    return _WHITESPACE_RE.sub("", value).strip().lower()


@register_normalizer("phone")
def normalize_phone(value: str, region: str = "DE") -> str:
    """E.164 where possible, digits otherwise.

    ``+49 170 1234567``, ``0170/1234567`` and ``+491701234567`` must all reach
    the same key, or the same person gets three placeholders.
    """
    cleaned = value.strip()
    try:
        parsed = phonenumbers.parse(cleaned, region)
    except phonenumbers.NumberParseException:
        parsed = None

    # is_valid_number, not is_possible_number: the lenient check accepts
    # "Aktenzeichen 2024" as +492024, which would give a case number a phone
    # placeholder. Every real German format passes the strict check.
    if parsed is not None and phonenumbers.is_valid_number(parsed):
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)

    # Unparseable, but still a phone number as far as the detector is concerned.
    # Falling back to bare digits keeps identical spellings together, which is
    # strictly better than giving up.
    fallback = _NON_PHONE_RE.sub("", cleaned)
    log.debug("phone not parseable as %s, falling back to digit form", region)
    return fallback or cleaned.casefold()


@register_normalizer("person")
def normalize_person(value: str) -> str:
    """NFKC, collapsed whitespace, leading titles removed, case-folded.

    ``casefold()`` maps ``ß`` to ``ss``, so ``Weiß`` and ``Weiss`` share one
    placeholder. That is intended: German law treats the two spellings as the
    same name, and each document still gets its own spelling back on reversal.
    """
    text = unicodedata.normalize("NFKC", value)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if not text:
        return ""

    tokens = text.split(" ")
    stripped = _drop_leading_titles(tokens)
    # Never normalise a name away entirely — a string made only of titles is
    # more likely a detector artefact than a person, but it still needs a key.
    return " ".join(stripped or tokens).casefold()


def _drop_leading_titles(tokens: list[str]) -> list[str]:
    index = 0
    while index < len(tokens):
        bare = tokens[index].strip(".,").casefold()
        if bare and bare in _TITLES:
            index += 1
            continue
        break
    return tokens[index:]


@register_normalizer("casefold")
def normalize_casefold(value: str) -> str:
    """NFKC, collapsed whitespace, case-folded.

    The catalogue's default when a type names no normalizer, so it must be the
    most conservative useful choice: it collapses spelling noise and nothing
    else.
    """
    text = unicodedata.normalize("NFKC", value)
    return _WHITESPACE_RE.sub(" ", text).strip().casefold()
