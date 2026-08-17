"""Pseudonymising text that already carries placeholders.

The CLI is right to refuse it: pseudonymising a document twice nests
placeholders and makes the result irreversible.

A gateway cannot afford that rule. A chat client replays its whole history on
every turn, so the moment one placeholder survives unrestored into the
transcript -- a model that mangled it, a value the answer never echoed back
cleanly -- every later turn carries it back in. Refusing there turns one
restoration miss into a conversation that can never recover: a hard 500 on
every subsequent message, which is exactly what a real Codex session hit.

So the gateway adopts them instead. A placeholder in the input is already
pseudonymous and is left exactly as it is; what changes is that the entity it
belongs to joins *this* request's mapping, so the answer can still be restored
against the one mapping the request owns.
"""

from __future__ import annotations

import pytest

from privaparse.parser.pseudonymizer import (
    AlreadyPseudonymizedError,
    pseudonymize_batch,
    pseudonymize_text,
)
from privaparse.parser.reverse_mapper import reverse_text


def _first_turn(repo, settings, fake_detector):
    return pseudonymize_text(
        "Hallo Max Mustermann", detector=fake_detector, repo=repo, settings=settings
    )


def test_the_default_still_refuses(repo, settings, fake_detector):
    """Unchanged for every existing caller. The CLI's rule is the right one
    for a document."""
    first = _first_turn(repo, settings, fake_detector)

    with pytest.raises(AlreadyPseudonymizedError):
        pseudonymize_batch(
            [first.text], detector=fake_detector, repo=repo, settings=settings
        )


def test_adopting_lets_a_replayed_turn_through(repo, settings, fake_detector):
    first = _first_turn(repo, settings, fake_detector)

    second = pseudonymize_batch(
        [first.text],
        detector=fake_detector,
        repo=repo,
        settings=settings,
        adopt_placeholders=True,
    )

    # Left exactly as it was: it is already pseudonymous.
    assert second.texts[0] == first.text
    assert second.mapping_id != first.mapping_id


def test_the_adopted_placeholder_restores_against_the_new_mapping(
    repo, settings, fake_detector
):
    """The point of adopting. Without it the answer to turn two would come
    back holding a placeholder only turn one could resolve."""
    first = _first_turn(repo, settings, fake_detector)
    placeholder = first.placeholders[0]

    second = pseudonymize_batch(
        [first.text], detector=fake_detector, repo=repo, settings=settings,
        adopt_placeholders=True,
    )
    restored = reverse_text(second.mapping_id, f"Antwort an {placeholder}", repo=repo)

    assert restored.text == "Antwort an Max Mustermann"


def test_new_entities_in_the_same_text_are_still_pseudonymised(
    repo, settings, fake_detector
):
    first = _first_turn(repo, settings, fake_detector)
    mixed = f"{first.text} und Erika Musterfrau"

    second = pseudonymize_batch(
        [mixed], detector=fake_detector, repo=repo, settings=settings,
        adopt_placeholders=True,
    )

    assert "Erika Musterfrau" not in second.texts[0]
    assert first.placeholders[0] in second.texts[0]


def test_a_placeholder_the_vault_never_issued_is_left_alone(
    repo, settings, fake_detector
):
    """Invented downstream, or from a vault that no longer exists. Nothing to
    adopt, and nothing to fail over."""
    result = pseudonymize_batch(
        ["Antwort an [[PERSON_ZZ9]]"],
        detector=fake_detector, repo=repo, settings=settings,
        adopt_placeholders=True,
    )

    assert result.texts[0] == "Antwort an [[PERSON_ZZ9]]"


def test_an_irreversible_placeholder_is_left_alone(repo, settings, fake_detector):
    """There is nothing to adopt: the vault knows the entity but stored no
    surface form for it, so no mapping can ever be given one to restore."""
    first = pseudonymize_text(
        "Zahlung mit Karte 4111 1111 1111 1111",
        detector=fake_detector, repo=repo, settings=settings,
    )
    card = first.placeholders[0]

    second = pseudonymize_batch(
        [first.text],
        detector=fake_detector, repo=repo, settings=settings,
        adopt_placeholders=True,
    )
    restored = reverse_text(second.mapping_id, f"Antwort zu {card}", repo=repo)

    assert second.texts[0] == first.text
    assert restored.irreversible == [card]
    assert restored.restored == 0


def test_an_existing_placeholder_is_never_detected_as_a_new_entity(
    repo, settings, fake_detector
):
    """A detector looking at `[[PERSON_A1]]` must not decide that is a name and
    wrap it in another placeholder -- that is the nesting the refusal exists to
    prevent, and adopting has to prevent it too."""
    first = _first_turn(repo, settings, fake_detector)

    second = pseudonymize_batch(
        [first.text], detector=fake_detector, repo=repo, settings=settings,
        adopt_placeholders=True,
    )

    assert second.texts[0].count("[[") == 1
