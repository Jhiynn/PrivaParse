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
_EXPIRY_RE = re.compile(r"^(0[1-9]|1[0-2])\s*[/\-.]\s*(\d{2}|\d{4})$")
_CVV_RE = re.compile(r"^\d{3,4}$")

register_validator("email_syntax")(is_valid_email)
register_validator("phone_shape")(is_plausible_phone)


def _compact(value: str) -> str:
    return _SEPARATORS_RE.sub("", value).upper()


@register_validator("iban_mod97")
def is_valid_iban(value: str) -> bool:
    """ISO 7064 mod-97-10.

    Length alone is not enough: a transposed pair of digits keeps the length
    and fails the checksum, and that is exactly the kind of near-miss a model
    produces when it grabs one character too many.
    """
    compact = _compact(value)
    if not 15 <= len(compact) <= 34 or not compact[:2].isalpha() or not compact[2:4].isdigit():
        return False
    rearranged = compact[4:] + compact[:4]
    digits = ""
    for character in rearranged:
        if character.isdigit():
            digits += character
        elif character.isalpha():
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
    """German Steuer-Identifikationsnummer: 11 digits, ISO 7064 MOD 11,10.

    The digit-repetition rule is checked too — exactly one digit appears twice
    or three times in the first ten, and that alone rejects most sequences a
    model mistakes for a tax number.
    """
    compact = _compact(value)
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


@register_validator("blz_de")
def is_valid_blz(value: str) -> bool:
    compact = _compact(value)
    return bool(_DIGITS_ONLY_RE.match(compact)) and len(compact) == 8


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
