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
from privaparse.parser import registry
from privaparse.parser.registry import register_normalizer
from privaparse.parser.validators import EXPIRY_FRAGMENT

log = get_logger("normalizer")

__all__ = [
    "normalize",
    "normalize_person",
    "normalize_email",
    "normalize_phone",
    "normalize_casefold",
    "normalize_strip_upper",
    "normalize_digits",
    "normalize_identity",
    "normalize_date_iso",
    "normalize_expiry",
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


_NON_DIGIT_RE = re.compile(r"\D")
_STRIP_RE = re.compile(r"[\s.\-/]")
_DATE_DMY_RE = re.compile(r"^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})$")
_DATE_ISO_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
#: Built from validators.py's own fragment rather than a second copy of the
#: same shape, so a value expiry_shape accepts can never fall outside what
#: this normalizer recognises.
_EXPIRY_RE = re.compile(rf"^{EXPIRY_FRAGMENT}$")


def normalize(value: str, normalizer: str) -> str:
    """Map a surface form onto its vault key using the named normalizer."""
    return registry.get_normalizer(normalizer)(value)


@register_normalizer("strip_upper")
def normalize_strip_upper(value: str) -> str:
    """Whitespace, dots, hyphens and slashes removed, then upper-cased.

    For values whose spacing is presentational: an IBAN printed in groups of
    four and the same IBAN printed solid are one value, and giving them two
    placeholders would make the document read as if two accounts were involved.
    """
    return _STRIP_RE.sub("", unicodedata.normalize("NFKC", value)).upper()


@register_normalizer("digits")
def normalize_digits(value: str) -> str:
    return _NON_DIGIT_RE.sub("", value)


@register_normalizer("identity")
def normalize_identity(value: str) -> str:
    """Unchanged. Only for irreversible types, whose key is hashed anyway."""
    return value


@register_normalizer("date_iso")
def normalize_date_iso(value: str) -> str:
    """``YYYY-MM-DD`` for the numeric forms, casefold for anything else.

    Deliberately shallow: month names vary by language and a wrong parse would
    merge two different dates onto one placeholder, which is worse than leaving
    "12. Maerz 2026" and "2026-03-12" as separate entries.
    """
    text = _WHITESPACE_RE.sub(" ", value).strip()
    match = _DATE_DMY_RE.match(text)
    if match:
        day, month, year = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    match = _DATE_ISO_RE.match(text)
    if match:
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    return text.casefold()


@register_normalizer("expiry")
def normalize_expiry(value: str) -> str:
    """``MM/YY``, regardless of separator or a 2- vs 4-digit year.

    date_iso does not recognise a two-part date at all — it requires a full
    day/month/year or year/month/day, so a card expiry falls through to its
    casefold fallback. expiry_shape accepts "/", "-" and "." as separators
    and both 2- and 4-digit years, so "08/27", "08-27", "08 / 27" and
    "08/2027" all pass the same validator while casefold gives every one of
    those spellings a different key — the same card expiring four times.
    """
    text = value.strip()
    match = _EXPIRY_RE.match(text)
    if not match:
        return text.casefold()
    month, year = match.groups()
    return f"{month}/{year[-2:]}"


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
