"""Checksum and syntax vetoes over what the model proposes.

The model decides what a span *is*; these decide whether that claim is
possible. They apply only to types whose syntax is fully decidable, and only
to spans the model produced — a backstop span is exact by construction.

There is deliberately no validator for SECRET, USERNAME, ADDRESS or PERSON. No
rule separates an API key from a random string, and a veto there would discard
real credentials. Those types are governed by their threshold alone.

At the precision the model reports across its full label set, these are the
highest-leverage precision mechanism in the pipeline, because unlike a raised
threshold they cost no recall at all.
"""

from __future__ import annotations

import ipaddress
import re

from privaparse.parser.detector import is_plausible_phone, is_valid_email
from privaparse.parser.registry import register_validator

_SEPARATORS_RE = re.compile(r"[\s.\-/]")
_DIGITS_ONLY_RE = re.compile(r"^\d+$")

#: A card expiry: MM then a 2- or 4-digit year, joined by "/", "-" or ".".
#: Shared as a bare fragment with normalizer.py's ``expiry`` normalizer, so a
#: shape this validator accepts can never silently fall outside what the
#: normalizer recognises — that gap is exactly what let "08/27", "08-27" and
#: "08 / 27" fragment into three placeholders for one expiry date before the
#: normalizer had a dedicated shape to canonicalise against.
EXPIRY_FRAGMENT = r"(0[1-9]|1[0-2])\s*[/\-.]\s*(\d{2}|\d{4})"
_EXPIRY_RE = re.compile(rf"^{EXPIRY_FRAGMENT}$")
_CVV_RE = re.compile(r"^\d{3,4}$")

#: "DE" plus 9 digits, no separators — a German VAT-ID (Umsatzsteuer-
#: Identifikationsnummer). Shared as a bare string, not just a compiled
#: pattern, because backstops.py's ``find_vat_de`` needs the same shape
#: wrapped in ``\b`` word boundaries instead of ``^…$`` anchors; importing a
#: fragment both build from is what keeps the two definitions from silently
#: drifting apart the way ``PLACEHOLDER_RE`` and a hand-written type-name
#: check once did.
VAT_DE_FRAGMENT = r"DE\d{9}"
_VAT_DE_RE = re.compile(rf"^{VAT_DE_FRAGMENT}$")

#: ISO 9362 BIC/SWIFT shape: 4-letter bank code, 2-letter country code,
#: 2-character alphanumeric location code, optional 3-character alphanumeric
#: branch code. 8 characters without a branch, 11 with one — nothing in
#: between is a real BIC.
_BIC_RE = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")

register_validator("email_syntax")(is_valid_email)
register_validator("phone_shape")(is_plausible_phone)


def _compact(value: str) -> str:
    return _SEPARATORS_RE.sub("", value).upper()


def _is_ascii_digit(character: str) -> bool:
    """``str.isdigit()`` also accepts non-ASCII digits — Arabic-Indic, super/
    subscript — which either crash ``int()`` or, worse, parse to a real value
    and let a look-alike character through the checksum unnoticed."""
    return "0" <= character <= "9"


def _is_ascii_upper(character: str) -> bool:
    """``str.isalpha()`` accepts letters far outside A-Z; folding one through
    ``ord(character) - ord("A")`` produces a number with no ISO 7064 meaning."""
    return "A" <= character <= "Z"


@register_validator("iban_mod97")
def is_valid_iban(value: str) -> bool:
    """ISO 7064 mod-97-10.

    Length alone is not enough: a transposed pair of digits keeps the length
    and fails the checksum, and that is exactly the kind of near-miss a model
    produces when it grabs one character too many.

    Character class is decided by explicit ASCII range, not ``str.isdigit()``
    / ``str.isalpha()``. Those are Unicode-aware: a superscript "²" reads as a
    digit and crashes the ``int()`` call below, and a letter like "Ä" reads as
    alphabetic and folds into the checksum as if it carried ISO 7064 meaning —
    which does not crash, and is worse, because a folded value can land back
    on a valid remainder by coincidence and wave through a string that was
    never a real IBAN.
    """
    compact = _compact(value)
    if (
        not 15 <= len(compact) <= 34
        or not all(_is_ascii_upper(c) for c in compact[:2])
        or not all(_is_ascii_digit(c) for c in compact[2:4])
    ):
        return False
    rearranged = compact[4:] + compact[:4]
    digits = ""
    for character in rearranged:
        if _is_ascii_digit(character):
            digits += character
        elif _is_ascii_upper(character):
            digits += str(ord(character) - ord("A") + 10)
        else:
            return False
    return int(digits) % 97 == 1


@register_validator("luhn")
def is_valid_card(value: str) -> bool:
    compact = _compact(value)
    if not _DIGITS_ONLY_RE.match(compact) or not 12 <= len(compact) <= 19:
        return False
    total = 0
    for index, character in enumerate(reversed(compact)):
        digit = int(character)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


@register_validator("tax_de")
def is_valid_tax_de(value: str) -> bool:
    """Accepts any shape the TAX_ID type actually covers: a Steuer-ID (with
    its checksum enforced), a Steuernummer, or a VAT-ID.

    TAX_ID routes two labels — ``tax_id`` and ``tax_number`` — and its
    prompt promises "Steuer-Identifikationsnummer" and "Steuernummer,
    Umsatzsteuer-Identifikationsnummer". The original version of this
    validator only ever checked the first of those three: a model-detected
    Steuernummer or VAT-ID was vetoed and leaked in clear, because it is not
    a Steuer-ID and was never going to pass that checksum. A validator has
    to cover the whole family a type routes, not just the one member with a
    convenient checksum.

    The Steuer-ID branch (``_is_valid_steuer_id``) is unchanged and stays
    exactly as strict — it is the one member of this family with a public,
    checkable algorithm, and weakening it would remove the only proof this
    validator can actually offer. Steuernummer is accepted by length alone
    (10 or 11 digits once separators are stripped): the format varies by
    Bundesland and has no single public checksum, so anything past a length
    check would be inventing a rule precise enough to reject real numbers —
    the exact failure this widening exists to close. One consequence worth
    naming: an 11-digit value that fails the Steuer-ID checksum is still
    accepted here, as a plausible Steuernummer. That is correct, not a
    loophole — the checksum was never a property Steuernummer had to begin
    with, so failing it says nothing about whether the value is real.
    """
    compact = _compact(value)
    if _is_valid_steuer_id(compact):
        return True
    if _DIGITS_ONLY_RE.match(compact) and len(compact) in (10, 11):
        return True
    return bool(_VAT_DE_RE.match(compact))


def _is_valid_steuer_id(compact: str) -> bool:
    """German Steuerliche Identifikationsnummer: 11 digits, ISO 7064 MOD 11,10.

    The digit-repetition rule is checked too — exactly one digit appears twice
    or three times in the first ten, and that alone rejects most sequences a
    model mistakes for a tax number. Takes an already-compacted string: its
    only caller has already stripped separators and upper-cased.
    """
    if not _DIGITS_ONLY_RE.match(compact) or len(compact) != 11:
        return False

    counts: dict[str, int] = {}
    for character in compact[:10]:
        counts[character] = counts.get(character, 0) + 1
    repeated = [count for count in counts.values() if count > 1]
    if len(counts) not in (9, 8) or len(repeated) != 1:
        return False

    remainder = 10
    for character in compact[:10]:
        total = (int(character) + remainder) % 10 or 10
        remainder = (2 * total) % 11
    check = (11 - remainder) % 10
    return check == int(compact[10])


@register_validator("bank_routing_de")
def is_valid_bank_routing_de(value: str) -> bool:
    """An eight-digit BLZ (Bankleitzahl), or a BIC in its standard shape.

    Renamed from ``blz_de``/``is_valid_blz``: ROUTING_NUMBER's own prompt
    promises "Bankleitzahlen, BIC, Routing-Nummern", but the old name and
    implementation covered only the first of those three, so a
    model-detected BIC — a routing identifier just as much as a BLZ — was
    vetoed and leaked in clear. ``DEUTDEFF``, ``DEUTDEFF500`` and
    ``COBADEFFXXX`` are all real BIC shapes an eight-digits-only check
    rejects outright.
    """
    compact = _compact(value)
    if _DIGITS_ONLY_RE.match(compact) and len(compact) == 8:
        return True
    return bool(_BIC_RE.match(compact))


@register_validator("postal_de")
def is_valid_postal_de(value: str) -> bool:
    compact = _compact(value)
    return bool(_DIGITS_ONLY_RE.match(compact)) and len(compact) == 5


@register_validator("ip_parse")
def is_valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip())
    except ValueError:
        return False
    return True


@register_validator("expiry_shape")
def is_valid_expiry(value: str) -> bool:
    return _EXPIRY_RE.match(value.strip()) is not None


@register_validator("cvv_shape")
def is_valid_cvv(value: str) -> bool:
    return _CVV_RE.match(value.strip()) is not None
