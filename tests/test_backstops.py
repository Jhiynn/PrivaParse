from __future__ import annotations

from privaparse.app.catalogue import load_catalogue
from privaparse.parser import registry
from privaparse.parser.detector import RegexDetector

TEXT = (
    "IBAN DE89 3704 0044 0532 0130 00, Karte 4111 1111 1111 1111, "
    "Server 192.168.0.1, USt-IdNr DE123456789, mail max@test.de, "
    "Tel +49 170 1234567. Bestellnummer 4711 vom 12.03.2026."
)


def _texts(name: str, text: str = TEXT) -> list[str]:
    return [text[a:b] for a, b in registry.get_backstop(name)(text)]


def test_iban_backstop_finds_the_iban_and_nothing_else():
    assert _texts("iban") == ["DE89 3704 0044 0532 0130 00"]


def test_iban_backstop_is_not_lost_to_an_adjacent_bic():
    # The greedy pattern used to swallow " BIC COBADEFFXXX" into the match,
    # mod97 rejected the overreached string whole, and the real IBAN was
    # dropped rather than narrowed — this is exactly what a German invoice or
    # remittance slip looks like.
    text = "IBAN DE89 3704 0044 0532 0130 00 BIC COBADEFFXXX"
    assert _texts("iban", text) == ["DE89 3704 0044 0532 0130 00"]


def test_iban_backstop_is_not_lost_before_the_word_sepa():
    text = "DE89 3704 0044 0532 0130 00 SEPA"
    assert _texts("iban", text) == ["DE89 3704 0044 0532 0130 00"]


def test_card_backstop_finds_the_card_and_not_the_order_number():
    found = _texts("card")
    assert "4111 1111 1111 1111" in found
    assert "4711" not in found


def test_card_backstop_is_not_lost_to_an_adjacent_expiry_date():
    # Same failure mode as the IBAN case: the pattern grabbed " 12" off
    # "12/25" too, Luhn rejected the 18-digit whole, and the real card number
    # was dropped rather than narrowed back to 16 digits.
    text = "Kreditkarte 4111 1111 1111 1111 12/25 gueltig."
    assert _texts("card", text) == ["4111 1111 1111 1111"]


def test_card_backstop_is_not_lost_to_trailing_digits():
    text = "Karte 4111 1111 1111 1111 99 Euro."
    assert _texts("card", text) == ["4111 1111 1111 1111"]


def test_ip_backstop_finds_the_address_and_not_the_date():
    found = _texts("ip")
    assert found == ["192.168.0.1"]


def test_vat_backstop_finds_the_vat_id():
    assert _texts("vat_de") == ["DE123456789"]


def test_backstops_only_run_for_enabled_types(tmp_path):
    override = tmp_path / "privaparse.entities.yaml"
    override.write_text(
        "version: 1\nplaceholder_types:\n  EMAIL:\n    enabled: false\n", encoding="utf-8"
    )
    detector = RegexDetector(load_catalogue(override))
    types = {span.type for span in detector.detect(TEXT)}
    assert "EMAIL" not in types
    assert "PHONE" in types


def test_backstop_spans_carry_the_placeholder_type():
    detector = RegexDetector(load_catalogue())
    for span in detector.detect(TEXT):
        assert span.type in {"EMAIL", "PHONE"}
        assert span.verify_against(TEXT)
