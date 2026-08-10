from __future__ import annotations

import pytest

from privaparse.parser import registry

VECTORS = [
    # (validator, value, expected)
    ("iban_mod97", "DE89370400440532013000", True),
    ("iban_mod97", "DE89 3704 0044 0532 0130 00", True),
    # Correct length and country, wrong check digits — the case a length-only
    # rule would wave through.
    ("iban_mod97", "DE90370400440532013000", False),
    ("iban_mod97", "DE8937040044053201300", False),
    # Superscript "2" is Unicode-digit-like (str.isdigit() is True) but not an
    # ASCII digit, so int() rejects it. Must return False, not raise.
    ("iban_mod97", "DE8937040044053201300²", False),
    # "Ä" is Unicode-alpha-like (str.isalpha() is True). Folded the old way
    # (ord("Ä") - ord("A") + 10 = 141) it still lands on remainder 1 mod 97 by
    # coincidence, so the old character-class dispatch wrongly accepted this —
    # a false accept, not a crash, and the more dangerous of the two.
    ("iban_mod97", "DE893Ä0400440532013000", False),
    ("luhn", "4111111111111111", True),
    ("luhn", "4111 1111 1111 1111", True),
    ("luhn", "4111111111111112", False),
    # Passes Luhn but is far too short to be a card.
    ("luhn", "18", False),
    # Steuer-ID: checksum enforced. This one vector alone cannot prove the
    # checksum stayed strict once the union also accepts Steuernummer shapes
    # (see below) — test_steuer_id_checksum_is_unweakened_by_the_wider_union
    # checks _is_valid_steuer_id directly, immune to that widening.
    ("tax_de", "36574261809", True),
    # Steuernummer shapes: none of these are valid Steuer-IDs (either the
    # wrong length, or, where the length coincides, the wrong checksum), but
    # TAX_ID's own prompt promises "Steuernummer" too, and the format has no
    # public checksum to check past its length.
    ("tax_de", "181/815/08155", True),
    ("tax_de", "18181508155", True),
    ("tax_de", "1337108153", True),
    ("tax_de", "13/371/08153", True),
    # 36574261808 is 36574261809 with the check digit flipped — previously
    # the only failure mode this validator could name. It is still not a
    # Steuer-ID, but it is still 11 plain digits, which is a valid
    # Steuernummer shape, so the union now accepts it: rejecting it would
    # mean rejecting a real Steuernummer merely for sharing a length with
    # the Steuer-ID format.
    ("tax_de", "36574261808", True),
    ("tax_de", "DE123456789", True),
    ("tax_de", "DE 123 456 789", True),
    # Near-misses that stay provably impossible under every branch.
    ("tax_de", "123456789", False),  # 9 digits: short of both the 10-11 range
    ("tax_de", "1234567890123", False),  # 13 digits: long past every branch
    ("tax_de", "DE12345678", False),  # VAT-ID shape, one digit short
    ("tax_de", "DE1234567890", False),  # VAT-ID shape, one digit too many
    ("tax_de", "FR123456789", False),  # right VAT-ID shape, wrong country
    ("bank_routing_de", "37040044", True),
    ("bank_routing_de", "3704004", False),
    # BIC shapes: ROUTING_NUMBER's own prompt promises "Bankleitzahlen, BIC,
    # Routing-Nummern", but the digits-only check (formerly registered as
    # blz_de) rejected all three of these real BIC shapes outright.
    ("bank_routing_de", "DEUTDEFF", True),
    ("bank_routing_de", "DEUTDEFF500", True),
    ("bank_routing_de", "COBADEFFXXX", True),
    ("bank_routing_de", "deutdeff", True),  # case-insensitive, like every other builtin here
    ("bank_routing_de", "DEUTDEF", False),  # 7 characters: neither 8 nor 11
    ("bank_routing_de", "DEUTDEFF50", False),  # branch code must be 0 or 3 characters, not 2
    ("bank_routing_de", "1EUTDEFF", False),  # digit in the bank-code position
    ("bank_routing_de", "DEUT12FF", False),  # digits in the country-code position
    ("postal_de", "50667", True),
    ("postal_de", "5066", False),
    ("ip_parse", "192.168.0.1", True),
    ("ip_parse", "2001:db8::1", True),
    ("ip_parse", "999.1.1.1", False),
    ("ip_parse", "1.2.3", False),
    ("expiry_shape", "03/28", True),
    ("expiry_shape", "03/2028", True),
    ("expiry_shape", "13/28", False),
    ("expiry_shape", "März", False),
    ("cvv_shape", "123", True),
    ("cvv_shape", "1234", True),
    ("cvv_shape", "12", False),
    ("cvv_shape", "12a", False),
    ("email_syntax", "max@test.de", True),
    ("email_syntax", "Systemmail", False),
    ("phone_shape", "+49 170 1234567", True),
    ("phone_shape", "2024", False),
]


@pytest.mark.parametrize("name, value, expected", VECTORS)
def test_validator_vectors(name, value, expected):
    assert registry.get_validator(name)(value) is expected


def test_steuer_id_checksum_is_unweakened_by_the_wider_tax_de_union():
    """tax_de's union of Steuer-ID / Steuernummer / VAT-ID shapes means an
    11-digit value that fails the Steuer-ID checksum is still accepted
    overall — it is still a plausible Steuernummer (see the "36574261808"
    case in VECTORS above). That must not be read as the checksum having
    gone soft: this calls the checksum function directly, where the
    Steuernummer branch cannot rescue a bad result, so a regression in the
    checksum itself cannot hide behind the union.
    """
    from privaparse.parser.validators import _is_valid_steuer_id

    assert _is_valid_steuer_id("36574261809") is True
    assert _is_valid_steuer_id("36574261808") is False
    assert _is_valid_steuer_id("12345678901") is False


def test_every_catalogue_validator_is_registered():
    from privaparse.app.catalogue import load_catalogue

    known = registry.known_validators()
    for placeholder in load_catalogue().types.values():
        if placeholder.validator is not None:
            assert placeholder.validator in known


def test_model_span_failing_its_validator_is_dropped():
    from privaparse.app.catalogue import load_catalogue
    from privaparse.parser.markdown import protect
    from privaparse.parser.merge import merge_spans
    from privaparse.parser.types import SOURCE_GLINER, Span

    text = "Bitte an Systemmail senden."
    protected = protect(text)
    bogus = Span(start=9, end=19, text="Systemmail", type="EMAIL",
                 score=0.99, source=SOURCE_GLINER)

    kept = merge_spans([bogus], protected=protected, catalogue=load_catalogue())
    assert kept == []


def test_backstop_span_is_not_second_guessed():
    from privaparse.app.catalogue import load_catalogue
    from privaparse.parser.markdown import protect
    from privaparse.parser.merge import merge_spans
    from privaparse.parser.types import SOURCE_REGEX, Span

    text = "Bitte an Systemmail senden."
    protected = protect(text)
    exact = Span(start=9, end=19, text="Systemmail", type="EMAIL",
                 score=1.0, source=SOURCE_REGEX)

    kept = merge_spans([exact], protected=protected, catalogue=load_catalogue())
    assert len(kept) == 1
