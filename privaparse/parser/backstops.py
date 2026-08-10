"""Regex finders that run alongside the model.

Their job is recall, not authority: a backstop span survives only where no
model span overlaps it. Every one of them is exact by construction, so the
validators in ``validators.py`` never second-guess them.

Each returns spans with an empty ``type``; the detector stamps the placeholder
type from the catalogue, because the same finder can serve a type the user
renamed.
"""

from __future__ import annotations

import re

import phonenumbers

from privaparse.parser.detector import _EMAIL_RE
from privaparse.parser.registry import register_backstop
from privaparse.parser.types import SOURCE_REGEX, Span
from privaparse.parser.validators import is_valid_card, is_valid_iban

_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{2,4}){2,8}\b")
# Separator sits *between* digits, not after the last one — ``(?:\d[ -]?){12,19}``
# lets the final optional separator ride along into the match, so "4111 1111
# 1111 1111 wurde" would capture the trailing space and glue the placeholder to
# the next word. Same 12-19 digit range, just anchored on a digit at both ends.
_CARD_RE = re.compile(r"\b\d(?:[ -]?\d){11,18}\b")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6_RE = re.compile(r"\b(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}\b")
_VAT_DE_RE = re.compile(r"\bDE\d{9}\b")

#: Phone matching uses STRICT_GROUPING, not VALID. The lenient level reads
#: German dates (04.06.2024) as phone numbers — measured on the gold set it
#: cost three false positives and 0.14 precision while finding no real numbers.
_PHONE_LENIENCY = phonenumbers.Leniency.STRICT_GROUPING


def _span(text: str, start: int, end: int) -> Span:
    return Span(start=start, end=end, text=text[start:end], type="", score=1.0,
                source=SOURCE_REGEX)


def _matches(text: str, pattern: re.Pattern[str], check=None) -> list[Span]:
    out: list[Span] = []
    for match in pattern.finditer(text):
        if check is not None and not check(match.group(0)):
            continue
        out.append(_span(text, match.start(), match.end()))
    return out


@register_backstop("email")
def find_emails(text: str) -> list[Span]:
    return _matches(text, _EMAIL_RE)


@register_backstop("phone")
def find_phones(text: str, region: str = "DE") -> list[Span]:
    found: dict[tuple[int, int], Span] = {}
    for match in phonenumbers.PhoneNumberMatcher(text, region, leniency=_PHONE_LENIENCY):
        found.setdefault((match.start, match.end), _span(text, match.start, match.end))
    return list(found.values())


@register_backstop("iban")
def find_ibans(text: str) -> list[Span]:
    """Checksum-gated. The pattern alone matches far too much."""
    return _matches(text, _IBAN_RE, is_valid_iban)


@register_backstop("card")
def find_cards(text: str) -> list[Span]:
    """Luhn-gated, which is what keeps order and invoice numbers out."""
    return _matches(text, _CARD_RE, is_valid_card)


@register_backstop("ip")
def find_ips(text: str) -> list[Span]:
    from privaparse.parser.validators import is_valid_ip

    return _matches(text, _IPV4_RE, is_valid_ip) + _matches(text, _IPV6_RE, is_valid_ip)


@register_backstop("vat_de")
def find_vat_de(text: str) -> list[Span]:
    return _matches(text, _VAT_DE_RE)
