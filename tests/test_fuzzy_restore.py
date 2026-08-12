"""Restoring a placeholder the model handed back slightly wrong.

Measured against a real model, `[[EMAIL_A1]]` comes back as `[EMAIL_A1]`, as
`[["EMAIL_A1"]]`, or written out as prose. Exact matching restores none of
them, so the caller sees a placeholder instead of their own data.

Opt-in, and the reason it can be opt-in safely is scope: a tolerant pattern is
built per placeholder *this mapping issued*, so the candidate set is the
handful of tokens from one request. A mangled placeholder belonging to another
session still matches nothing, which is the property the whole vault rests on
and the one these tests exist to keep.
"""

from __future__ import annotations

import pytest

from privaparse.parser.pseudonymizer import pseudonymize_text
from privaparse.parser.reverse_mapper import reverse_text


@pytest.fixture()
def issued(repo, settings, fake_detector):
    """One mapping holding `[[PERSON_A1]]` for Max Mustermann."""
    result = pseudonymize_text(
        "Hallo Max Mustermann",
        detector=fake_detector,
        repo=repo,
        settings=settings,
    )
    placeholder = result.placeholders[0]
    return result.mapping_id, placeholder


@pytest.mark.parametrize(
    "mangled",
    [
        "[PERSON_A1]",          # one bracket pair dropped -- the common one
        "[[PERSON_A1]",         # unbalanced
        "[PERSON_A1]]",         # unbalanced the other way
        '[["PERSON_A1"]]',      # quotes injected inside
        "[[ PERSON_A1 ]]",      # padded
        "PERSON_A1",            # brackets gone entirely
        "Person A1",            # prose-ified: case lost, underscore became a space
    ],
)
def test_a_mangled_placeholder_is_restored_when_fuzzy_is_on(repo, issued, mangled):
    mapping_id, _ = issued

    result = reverse_text(mapping_id, f"Antwort an {mangled}.", repo=repo, fuzzy=True)

    assert result.text == "Antwort an Max Mustermann."
    assert result.recovered == 1


def test_the_same_text_restores_nothing_with_fuzzy_off(repo, issued):
    """The default stays exact. Opt-in means opt-in."""
    mapping_id, _ = issued

    result = reverse_text(mapping_id, "Antwort an [PERSON_A1].", repo=repo)

    assert result.text == "Antwort an [PERSON_A1]."
    assert result.restored == 0


def test_an_exact_placeholder_still_counts_as_exact(repo, issued):
    mapping_id, placeholder = issued

    result = reverse_text(mapping_id, f"Antwort an {placeholder}.", repo=repo, fuzzy=True)

    assert result.text == "Antwort an Max Mustermann."
    assert (result.restored, result.recovered) == (1, 0)


def test_a_mangled_placeholder_from_another_session_is_not_restored(
    repo, settings, fake_detector, issued
):
    """The property the vault rests on, checked against the loosened matcher.

    Fuzzy matching widens *how* a placeholder may be written, never *which*
    placeholders a mapping may resolve. A session that never issued
    `[[PERSON_A1]]` must not restore it however it is spelled.
    """
    mapping_id, _ = issued
    other = pseudonymize_text(
        "Hallo Erika Musterfrau", detector=fake_detector, repo=repo, settings=settings
    )

    result = reverse_text(other.mapping_id, "Antwort an [PERSON_A1].", repo=repo, fuzzy=True)

    assert "Max Mustermann" not in result.text
    assert result.text == "Antwort an [PERSON_A1]."


def test_ordinary_prose_is_left_alone(repo, issued):
    """`Person` on its own is a word, not a placeholder. Only the type *and*
    its suffix together are distinctive enough to match."""
    mapping_id, _ = issued

    result = reverse_text(
        mapping_id,
        "Diese Person hat angerufen. Person A2 ist jemand anderes.",
        repo=repo,
        fuzzy=True,
    )

    assert result.text == "Diese Person hat angerufen. Person A2 ist jemand anderes."
    assert result.recovered == 0


def test_a_placeholder_inside_a_longer_word_is_not_matched(repo, issued):
    mapping_id, _ = issued

    result = reverse_text(mapping_id, "XPERSON_A1X", repo=repo, fuzzy=True)

    assert result.text == "XPERSON_A1X"


def test_a_value_with_regex_characters_survives_substitution(
    repo, settings, fake_detector
):
    """The replacement is inserted literally. A name holding a backslash or a
    group reference must not be interpreted as one."""
    from privaparse.parser.detector import StaticDetector
    from privaparse.parser.types import SOURCE_GLINER, EntityType, Span

    awkward = r"Max \1 Mustermann$"
    text = f"Hallo {awkward}"
    detector = StaticDetector([
        Span(start=6, end=6 + len(awkward), text=awkward, type=EntityType.PERSON,
             score=0.99, source=SOURCE_GLINER)
    ])
    result = pseudonymize_text(text, detector=detector, repo=repo, settings=settings)
    placeholder = result.placeholders[0]
    mangled = placeholder.strip("[]")

    restored = reverse_text(result.mapping_id, f"An {mangled}.", repo=repo, fuzzy=True)

    assert restored.text == f"An {awkward}."
