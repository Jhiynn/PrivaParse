"""Pseudonymising already-pseudonymised text must fail, not silently nest."""

from __future__ import annotations

import pytest

from privaparse.engine import PrivaParseEngine
from privaparse.parser.pseudonymizer import AlreadyPseudonymizedError


def test_pseudonymising_twice_is_refused(engine: PrivaParseEngine, beispiel_md: str) -> None:
    once = engine.pseudonymize(beispiel_md)

    with pytest.raises(AlreadyPseudonymizedError):
        engine.pseudonymize(once.text)


def test_text_containing_a_stray_placeholder_is_refused(engine: PrivaParseEngine) -> None:
    """Even if it was never issued by this vault: the output would be ambiguous
    on the way back."""
    with pytest.raises(AlreadyPseudonymizedError):
        engine.pseudonymize("Bitte an [[PERSON_ZZ9]] weiterleiten.")


def test_placeholder_lookalikes_do_not_block_pseudonymisation(
    engine: PrivaParseEngine,
) -> None:
    text = "Siehe [[wiki link]] und [[lower_a1]] — Kontakt: Max Mustermann."
    result = engine.pseudonymize(text)

    assert "[[wiki link]]" in result.text
    assert "[[lower_a1]]" in result.text
    assert "[[PERSON_A1]]" in result.text


def test_the_error_says_what_to_do_instead(engine: PrivaParseEngine, beispiel_md: str) -> None:
    once = engine.pseudonymize(beispiel_md)
    with pytest.raises(AlreadyPseudonymizedError) as excinfo:
        engine.pseudonymize(once.text)
    assert "Reverse it first" in str(excinfo.value)


def test_a_refused_run_leaves_no_trace_in_the_vault(
    engine: PrivaParseEngine, beispiel_md: str
) -> None:
    once = engine.pseudonymize(beispiel_md)
    before = engine.vault_stats()

    with pytest.raises(AlreadyPseudonymizedError):
        engine.pseudonymize(once.text)

    after = engine.vault_stats()
    assert (after.entities, after.mappings) == (before.entities, before.mappings)
