"""Regex finders that run alongside the model.

Their job is recall, not authority: a backstop span survives only where no
model span overlaps it. Precision varies by finder: IBAN, card and IP are
checksum- or parse-gated, but ``vat_de`` is a shape match with no checksum to
run, and email is a heuristic pattern like any other. ``validators.py`` never
second-guesses a backstop span regardless — it only re-checks what the model
proposes, on the theory that a rule should not be asked to grade its own
output.

Each returns raw ``(start, end)`` offsets, not a typed ``Span``: a finder does
not know which placeholder it is serving, because the same finder can serve a
type the user renamed. ``RegexDetector`` is the only place that builds a
``Span``, stamping the type from the catalogue.
"""

from __future__ import annotations

import re

import phonenumbers

from privaparse.parser.detector import _EMAIL_RE
from privaparse.parser.registry import register_backstop
from privaparse.parser.validators import is_valid_card, is_valid_iban

_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{2,4}){2,8}\b")
_CARD_RE = re.compile(r"\b\d(?:[ -]?\d){11,18}\b")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6_RE = re.compile(r"\b(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}\b")
_VAT_DE_RE = re.compile(r"\bDE\d{9}\b")

#: Phone matching uses STRICT_GROUPING, not VALID. The lenient level reads
#: German dates (04.06.2024) as phone numbers — measured on the gold set it
#: cost three false positives and 0.14 precision while finding no real numbers.
_PHONE_LENIENCY = phonenumbers.Leniency.STRICT_GROUPING


def _matches(text: str, pattern: re.Pattern[str], check=None) -> list[tuple[int, int]]:
    """Find every accepted candidate, peeling back what the gate rejects.

    Uses an explicit cursor rather than ``finditer``. A truncated candidate's
    accepted end can fall well short of the raw greedy match's end, and a
    second genuine value can sit in that swallowed gap — two IBANs back to
    back, where the raw match reaches from the first into the second.
    ``finditer`` resumes past its own raw match regardless of how much of it
    we kept, which would consume the second value's prefix and lose it;
    resuming at what was actually accepted lets it be found on its own terms.

    ``pattern`` must match at least one character. ``search`` clamps a ``pos``
    past the end of ``text`` back to ``len(text)`` instead of returning
    ``None``, so a zero-width-capable pattern (``\\d*``) would return the same
    empty match there no matter how far ``pos`` is pushed forward — the
    ``pos <= len(text)`` bound below is what stops that from being a fixed
    point, not anything ``search`` itself guarantees.
    """
    out: list[tuple[int, int]] = []
    pos = 0
    while pos <= len(text):
        match = pattern.search(text, pos)
        if match is None:
            break
        start, end = match.start(), match.end()
        # A greedy pattern can overreach into a following token — an IBAN
        # swallowing "BIC", a card swallowing an expiry digit or two. The gate
        # rejecting the whole match does not mean nothing here is real: peel
        # back to the last separator and retry before giving up on it.
        while check is not None and not check(text[start:end]):
            cut = max(text.rfind(" ", start, end), text.rfind("-", start, end))
            if cut <= start:
                end = start
                break
            end = cut
        if end > start:
            out.append((start, end))
            pos = end
        else:
            # Nothing validated at any truncation. Advance past this match's
            # start, not its swallowed end, so a genuine value beginning right
            # where this attempt did still gets a search of its own — and so
            # the cursor still moves even when nothing here was ever real.
            pos = start + 1
    return out


@register_backstop("email")
def find_emails(text: str) -> list[tuple[int, int]]:
    return _matches(text, _EMAIL_RE)


@register_backstop("phone")
def find_phones(text: str, region: str = "DE") -> list[tuple[int, int]]:
    # Single-region by design: a backstop is (text) -> offsets with nothing
    # else in the call, so RegexDetector has no channel to carry a per-type
    # region into it. A second region belongs in a catalogue options: map, not
    # a detector that no longer knows what a phone number is — the old
    # extra_regions capability is dropped along with that, not just unwired.
    found: dict[tuple[int, int], tuple[int, int]] = {}
    for match in phonenumbers.PhoneNumberMatcher(text, region, leniency=_PHONE_LENIENCY):
        found.setdefault((match.start, match.end), (match.start, match.end))
    return list(found.values())


@register_backstop("iban")
def find_ibans(text: str) -> list[tuple[int, int]]:
    """Checksum-gated. The pattern alone matches far too much."""
    return _matches(text, _IBAN_RE, is_valid_iban)


@register_backstop("card")
def find_cards(text: str) -> list[tuple[int, int]]:
    """Luhn-gated, which is what keeps order and invoice numbers out."""
    return _matches(text, _CARD_RE, is_valid_card)


@register_backstop("ip")
def find_ips(text: str) -> list[tuple[int, int]]:
    from privaparse.parser.validators import is_valid_ip

    return _matches(text, _IPV4_RE, is_valid_ip) + _matches(text, _IPV6_RE, is_valid_ip)


@register_backstop("vat_de")
def find_vat_de(text: str) -> list[tuple[int, int]]:
    return _matches(text, _VAT_DE_RE)
