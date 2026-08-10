"""Normalisation decides what counts as "the same value" in the vault."""

from __future__ import annotations

import pytest

from privaparse.parser.normalizer import (
    normalize,
    normalize_email,
    normalize_person,
    normalize_phone,
)
from privaparse.parser.types import EntityType


@pytest.mark.parametrize(
    "raw",
    [
        "+49 170 1234567",
        "+49 (0) 170 1234567",
        "0170 1234567",
        "0170/1234567",
        "0170-1234567",
        "+491701234567",
    ],
)
def test_all_german_spellings_of_one_number_share_a_key(raw: str) -> None:
    """Different spellings must not become different placeholders."""
    assert normalize_phone(raw) == "+491701234567"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Durchwahl 12", "12"), ("Aktenzeichen 2024", "2024"), ("Rechnung 4711", "4711")],
)
def test_non_numbers_are_not_dressed_up_as_phone_numbers(raw: str, expected: str) -> None:
    """The lenient `is_possible_number` turns "Aktenzeichen 2024" into +492024.
    The strict check keeps case numbers out of the phone namespace."""
    assert normalize_phone(raw) == expected


def test_phone_region_is_configurable() -> None:
    assert normalize_phone("0664 1234567", region="AT").startswith("+43")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Max@Test.DE", "max@test.de"),
        ("  max@test.de  ", "max@test.de"),
        ("MAX@TEST.DE", "max@test.de"),
    ],
)
def test_emails_are_case_folded_and_trimmed(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "Max Mustermann",
        "MAX MUSTERMANN",
        "max mustermann",
        "Dr. Max Mustermann",
        "Prof. Dr. med. Max Mustermann",
        "Herr Max Mustermann",
        "Herrn Dr. Max Mustermann",
        "Max  Mustermann",
        " Max Mustermann ",
    ],
)
def test_titles_and_casing_collapse_onto_one_person(raw: str) -> None:
    assert normalize_person(raw) == "max mustermann"


def test_umlauts_survive_normalisation() -> None:
    assert normalize_person("Müller-Lüdenscheidt") == "müller-lüdenscheidt"
    assert normalize_person("Jörg Öztürk") == "jörg öztürk"


def test_eszett_and_double_s_are_the_same_person() -> None:
    """Casefolding maps ß to ss. German law treats the spellings as one name,
    and each document still gets its own spelling restored."""
    assert normalize_person("Weiß") == normalize_person("Weiss")


def test_compatibility_forms_are_unified() -> None:
    """NFKC so a decomposed umlaut and a precomposed one are one person."""
    decomposed = "Müller"
    precomposed = "Müller"
    assert normalize_person(decomposed) == normalize_person(precomposed)


def test_nobility_particles_are_kept() -> None:
    """"von" is part of the legal name; dropping it would merge two people."""
    assert normalize_person("Max von Bergen") == "max von bergen"
    assert normalize_person("Max von Bergen") != normalize_person("Max Bergen")


def test_a_name_made_only_of_titles_still_gets_a_key() -> None:
    assert normalize_person("Dr. Prof.") != ""


def test_empty_input_normalises_to_empty() -> None:
    assert normalize_person("   ") == ""


def test_normalize_dispatches_on_type() -> None:
    assert normalize("MAX@TEST.DE", EntityType.EMAIL) == "max@test.de"
    assert normalize("0170 1234567", EntityType.PHONE) == "+491701234567"
    assert normalize("Dr. Max Mustermann", EntityType.PERSON) == "max mustermann"
