from __future__ import annotations

import pytest

from privaparse.database.placeholder import (
    build_placeholder,
    contains_placeholder,
    encode_suffix,
    find_placeholders,
)


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        (0, "A1"),
        (1, "A2"),
        (8, "A9"),
        (9, "B1"),
        (17, "B9"),
        (18, "C1"),
        (25 * 9, "Z1"),
        (25 * 9 + 8, "Z9"),
        (26 * 9, "AA1"),
        (26 * 9 + 9, "AB1"),
    ],
)
def test_encode_suffix_sequence(index: int, expected: str) -> None:
    assert encode_suffix(index) == expected


def test_encode_suffix_is_injective_over_a_long_run() -> None:
    seen = {encode_suffix(i) for i in range(1000)}
    assert len(seen) == 1000


def test_encode_suffix_rejects_negative() -> None:
    with pytest.raises(ValueError):
        encode_suffix(-1)


def test_build_placeholder_matches_spec_format() -> None:
    assert build_placeholder("PERSON", 0) == "[[PERSON_A1]]"
    assert build_placeholder("EMAIL", 1) == "[[EMAIL_A2]]"
    assert build_placeholder("PHONE", 2) == "[[PHONE_A3]]"


def test_find_placeholders_extracts_type_and_suffix() -> None:
    text = "Hallo [[PERSON_A1]], schreib an [[EMAIL_A2]] oder [[PHONE_AB12]]."
    matches = find_placeholders(text)
    assert [(m.group(1), m.group(2)) for m in matches] == [
        ("PERSON", "A1"),
        ("EMAIL", "A2"),
        ("PHONE", "AB12"),
    ]


def test_find_placeholders_ignores_lookalikes() -> None:
    # Markdown link syntax and wiki links must not be mistaken for placeholders.
    text = "[[nicht_das]] und [[lower_a1]] und [[PERSON-A1]] und [ref][x]"
    assert find_placeholders(text) == []
    assert contains_placeholder(text) is False
