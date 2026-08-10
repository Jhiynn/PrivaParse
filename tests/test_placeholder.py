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


# --- PLACEHOLDER_RE's type group allows underscores -------------------------
#
# The type-name grammar changed from [A-Z][A-Z0-9]* to [A-Z][A-Z0-9_]* so a
# catalogue type like NATIONAL_ID can render as [[NATIONAL_ID_A1]] and still
# read as words. The suffix grammar ([A-Z]+[0-9]+) never contains an
# underscore, so the split stays unambiguous: the *last* underscore in a
# rendered placeholder is always the one format_placeholder inserted, and a
# greedy type group backtracks onto exactly that one. These tests prove it
# rather than assert it.


def test_single_word_type_still_parses() -> None:
    """The plain case must not regress: no internal underscore at all."""
    matches = find_placeholders("Hallo [[PERSON_A1]].")
    assert [(m.group(1), m.group(2)) for m in matches] == [("PERSON", "A1")]


def test_multi_word_type_parses_with_the_full_type_and_right_suffix() -> None:
    matches = find_placeholders("Ihre Kennung: [[NATIONAL_ID_A1]].")
    assert [(m.group(1), m.group(2)) for m in matches] == [("NATIONAL_ID", "A1")]
    # Group 1 is the whole type, not truncated at the first underscore.
    assert matches[0].group(1) != "NATIONAL"


def test_type_name_ending_in_a_digit_still_splits_correctly() -> None:
    # No shipped type ends in a digit, but TYPE_NAME_RE allows one, and a
    # digit right before the separator underscore is the exact shape that
    # would be ambiguous with the suffix's own ``[0-9]+`` tail if the split
    # were resolved the wrong way round.
    matches = find_placeholders("[[REGION7_A1]]")
    assert [(m.group(1), m.group(2)) for m in matches] == [("REGION7", "A1")]


def test_multi_letter_suffix_block_parses() -> None:
    # "AA11" is not a shape encode_suffix currently produces (its digit block
    # is always a single digit 1-9), but PLACEHOLDER_RE's suffix group,
    # [A-Z]+[0-9]+, has to accept it on its own grammar regardless of what the
    # generator happens to emit today.
    matches = find_placeholders("[[PERSON_AA11]]")
    assert [(m.group(1), m.group(2)) for m in matches] == [("PERSON", "AA11")]


def test_every_shipped_type_round_trips_through_build_and_find() -> None:
    """The test that would have caught the original defect directly: for
    every type the shipped catalogue actually defines, build a placeholder
    and parse it back, across several suffix shapes (single-letter block,
    a different single-letter block, the last single-letter block, and the
    first double-letter block). A type with an internal underscore
    (NATIONAL_ID, DATE_OF_BIRTH, ...) must parse back to exactly itself, not
    a prefix truncated at its first underscore.
    """
    from privaparse.app.catalogue import load_catalogue

    type_names = sorted(load_catalogue().types)
    assert len(type_names) == 25  # sanity: this is the real, full catalogue

    for name in type_names:
        for index in (0, 9, 233, 234):  # A1, B1, Z9, AA1
            placeholder = build_placeholder(name, index)
            matches = find_placeholders(placeholder)
            assert len(matches) == 1, f"{placeholder!r} did not parse as one placeholder"
            match = matches[0]
            assert match.group(0) == placeholder
            assert match.group(1) == name
            assert match.group(2) == encode_suffix(index)
