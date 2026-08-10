"""Rule-based detection for email and phone.

These two types are the control group of the whole project: if they sit near
1.0 in the eval and PERSON does not, the model is the problem, not the pipeline.
So they need to be right.
"""

from __future__ import annotations

import pytest

from privaparse.app.catalogue import load_catalogue
from privaparse.parser.detector import RegexDetector
from privaparse.parser.types import EntityType


@pytest.fixture()
def detector() -> RegexDetector:
    return RegexDetector(load_catalogue())


def _texts_of(spans, entity_type):
    return [s.text for s in spans if s.type == entity_type]


@pytest.mark.parametrize(
    "raw",
    [
        "+49 170 1234567",
        "+49 (0) 170 1234567",
        "0170 1234567",
        "0170/1234567",
        "0170-1234567",
        "+491701234567",
        "030 123456",
        "+49 30 123456",
    ],
)
def test_german_phone_formats_are_found(detector: RegexDetector, raw: str) -> None:
    spans = detector.detect(f"Erreichbar unter {raw} tagsüber.")
    phones = _texts_of(spans, EntityType.PHONE)
    assert phones, f"no phone detected in {raw!r}"
    assert raw in phones[0]


@pytest.mark.parametrize(
    "raw",
    [
        "max@test.de",
        "Max.Mustermann@firma-gmbh.de",
        "m.mustermann+news@sub.domain.co.uk",
        "vorname_nachname@x.io",
    ],
)
def test_email_formats_are_found(detector: RegexDetector, raw: str) -> None:
    spans = detector.detect(f"Kontakt: {raw} — danke.")
    assert _texts_of(spans, EntityType.EMAIL) == [raw]


def test_email_span_offsets_point_at_the_email(detector: RegexDetector) -> None:
    text = "Bitte an max@test.de schicken."
    span = detector.detect(text)[0]
    assert text[span.start : span.end] == span.text == "max@test.de"


def test_trailing_sentence_period_is_not_part_of_the_email(detector: RegexDetector) -> None:
    spans = detector.detect("Schreib an max@test.de.")
    assert _texts_of(spans, EntityType.EMAIL) == ["max@test.de"]


@pytest.mark.parametrize(
    "text",
    [
        "Version 1.2.3 und @handle allein",
        "Der Preis liegt bei 12,50 EUR",
        "Kein Kontakt hinterlegt.",
    ],
)
def test_plain_text_produces_no_false_positives(detector: RegexDetector, text: str) -> None:
    assert detector.detect(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "Am 04.06.2024 rief die Kollegin an.",
        "Rechnung Nr. 4711 vom 03.02.2024",
        "Bescheid vom 02.05.2024, Widerspruch möglich.",
        "Aktenzeichen: 12 O 3456/23",
        "Kundennummer: 0170 8899",
        "Betrag: 1.249,00 EUR",
        "Angebot 2024-0912 liegt vor.",
        "Vorgang 2024/1187 abgeschlossen.",
    ],
)
def test_dates_and_reference_numbers_are_not_phone_numbers(
    detector: RegexDetector, text: str
) -> None:
    """Found by the gold-set eval: the lenient matcher read `04.06.2024` as a
    phone number, which would pseudonymise every date in a file note."""
    assert _texts_of(detector.detect(text), EntityType.PHONE) == []


def test_multiple_entities_in_one_document(detector: RegexDetector) -> None:
    text = "Max: max@test.de oder +49 170 1234567. Erika: erika@test.de."
    spans = detector.detect(text)
    assert sorted(_texts_of(spans, EntityType.EMAIL)) == ["erika@test.de", "max@test.de"]
    assert len(_texts_of(spans, EntityType.PHONE)) == 1
    for span in spans:
        assert span.verify_against(text)


def test_repeated_value_yields_one_span_per_occurrence(detector: RegexDetector) -> None:
    text = "max@test.de und nochmal max@test.de"
    assert len(_texts_of(detector.detect(text), EntityType.EMAIL)) == 2
