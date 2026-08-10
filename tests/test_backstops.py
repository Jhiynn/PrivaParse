from __future__ import annotations

import re
import threading

from privaparse.app.catalogue import load_catalogue
from privaparse.parser import registry
from privaparse.parser.backstops import _matches
from privaparse.parser.detector import RegexDetector

TEXT = (
    "IBAN DE89 3704 0044 0532 0130 00, Karte 4111 1111 1111 1111, "
    "Server 192.168.0.1, USt-IdNr DE123456789, mail max@test.de, "
    "Tel +49 170 1234567. Bestellnummer 4711 vom 12.03.2026."
)


def _texts(name: str, text: str = TEXT) -> list[str]:
    return [text[a:b] for a, b in registry.get_backstop(name)(text)]


def test_matches_terminates_on_a_zero_width_capable_pattern():
    """``_matches`` must stay safe for a finder this codebase hasn't written
    yet: none of the six registered patterns are zero-width-capable, so
    nothing else here exercises this path. ``pattern.search`` clamps a
    cursor past the end of the text back to ``len(text)`` instead of
    returning ``None``, so a naive "advance past the match" cursor can get
    stuck re-finding the same empty match forever. Runs in its own daemon
    thread with a hard timeout so a regression here fails the suite instead
    of freezing it.
    """
    result: list[list[tuple[int, int]]] = []

    def run() -> None:
        result.append(_matches("abc123def", re.compile(r"\d*")))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive(), "_matches hung on a zero-width-capable pattern"
    assert result == [[(3, 6)]]


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


def test_iban_backstop_finds_two_identical_ibans_back_to_back():
    # The raw greedy match used to reach from the first IBAN into the second
    # — "DE89" is itself a valid [A-Z0-9]{2,4} repeat group, so the pattern
    # kept going. Peeling back correctly narrowed the *emitted* candidate to
    # the first IBAN, but finditer still resumed past the raw match's end,
    # which sat inside the second IBAN — consuming its prefix and losing it.
    # A test that only checked the first value would not catch this.
    text = "DE89 3704 0044 0532 0130 00 DE89 3704 0044 0532 0130 00"
    assert _texts("iban", text) == ["DE89 3704 0044 0532 0130 00"] * 2


def test_iban_backstop_finds_two_different_ibans_back_to_back():
    text = "DE89 3704 0044 0532 0130 00 GB29 NWBK 6016 1331 9268 19"
    assert _texts("iban", text) == [
        "DE89 3704 0044 0532 0130 00",
        "GB29 NWBK 6016 1331 9268 19",
    ]


def test_iban_backstop_finds_three_ibans_in_a_row():
    text = "DE89 3704 0044 0532 0130 00 " * 3
    assert _texts("iban", text) == ["DE89 3704 0044 0532 0130 00"] * 3


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


def test_card_backstop_finds_two_adjacent_cards_of_different_length():
    # Same cursor-resume bug as the IBAN case, reproduced for card — but two
    # identical 16-digit, evenly-grouped cards do not trigger it: the pattern's
    # 19-digit cap forces only a 3-digit overreach past a 16-digit card, which
    # never lands on a \b boundary inside a 4-4-4-4 grouped second card, so the
    # regex backtracks itself back to the first card's own edge with no help
    # from the peel-back. A 15-digit card followed by a 4-grouped 16-digit one
    # does trigger it: the cap's 4-digit overreach lands exactly on the second
    # card's first group boundary, the gate rejects the 19-digit whole,
    # peel-back correctly narrows to the first card — and finditer used to
    # resume past the raw match anyway, consuming the second card's leading
    # "4111" and losing it.
    text = "378282246310005 4111 1111 1111 1111"
    assert _texts("card", text) == ["378282246310005", "4111 1111 1111 1111"]


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
    # Before the full catalogue (Task 9), EMAIL and PHONE were the only types
    # with a backstop wired in, so this used to be the entire set. IBAN, CARD,
    # IP and TAXID (vat_de) now have one too, and TEXT was always built to
    # exercise all six registered backstop patterns — see the "six registered
    # patterns" comment above. Bestellnummer 4711 and 12.03.2026 are decoys
    # that must keep finding nothing.
    detector = RegexDetector(load_catalogue())
    for span in detector.detect(TEXT):
        assert span.type in {"EMAIL", "PHONE", "IBAN", "CARD", "IP", "TAXID"}
        assert span.verify_against(TEXT)
