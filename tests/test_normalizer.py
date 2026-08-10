"""Normalisation decides what counts as "the same value" in the vault."""

from __future__ import annotations

import pytest

from privaparse.parser.normalizer import (
    normalize,
    normalize_email,
    normalize_person,
    normalize_phone,
)


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


def test_normalize_dispatches_by_registered_name() -> None:
    """normalize() takes a registry name, not an entity type — the catalogue
    supplies the name, so dispatch must key off exactly what it stores."""
    assert normalize("MAX@TEST.DE", "email") == "max@test.de"
    assert normalize("0170 1234567", "phone") == "+491701234567"
    assert normalize("Dr. Max Mustermann", "person") == "max mustermann"


@pytest.mark.parametrize(
    "name, raw, expected",
    [
        ("strip_upper", "DE89 3704 0044 0532 0130 00", "DE89370400440532013000"),
        ("strip_upper", "de89-3704-0044", "DE89370400 44".replace(" ", "")),
        ("digits", "4111 1111-1111 1111", "4111111111111111"),
        ("digits", "CVV: 123", "123"),
        # str.casefold() maps ß to ss, same as normalize_person relies on
        # (normalizer.py); "musterstraße" would be str.lower(), not this.
        ("casefold", "  Musterstraße   5 ", "musterstrasse 5"),
        ("identity", "  Sk-Live-XYZ ", "  Sk-Live-XYZ "),
        ("date_iso", "12.03.2026", "2026-03-12"),
        ("date_iso", "2026-03-12", "2026-03-12"),
        ("date_iso", "12. Maerz 2026", "12. maerz 2026"),
    ],
)
def test_registered_normalizers(name, raw, expected):
    assert normalize(raw, name) == expected


def test_two_spellings_of_one_iban_collide():
    assert normalize("DE89 3704 0044", "strip_upper") == normalize("de89370400.44", "strip_upper")


def test_two_distinct_ibans_do_not_collide():
    assert normalize("DE89 3704", "strip_upper") != normalize("DE89 3705", "strip_upper")


def test_unknown_normalizer_name_raises():
    with pytest.raises(KeyError, match="normalizer"):
        normalize("x", "does_not_exist")
