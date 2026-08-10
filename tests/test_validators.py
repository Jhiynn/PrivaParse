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
    ("tax_de", "36574261809", True),
    ("tax_de", "36574261808", False),
    ("tax_de", "12345678901", False),
    ("blz_de", "37040044", True),
    ("blz_de", "3704004", False),
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
