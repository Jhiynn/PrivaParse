"""The Phase 1 MVP checklist, verified end to end.

Detect a person, an email and a phone number; generate placeholders; store the
mapping; restore the original text; confirm every placeholder came back.
"""

from __future__ import annotations

from privaparse.app.mock_llm import mock_llm_response
from privaparse.engine import PrivaParseEngine
from privaparse.parser.types import EntityType


def test_detects_person_email_and_phone(engine: PrivaParseEngine, beispiel_md: str) -> None:
    spans = engine.detect(beispiel_md)
    found = {span.type for span in spans}
    assert found == {EntityType.PERSON, EntityType.EMAIL, EntityType.PHONE}


def test_generates_the_placeholders_from_the_spec(
    engine: PrivaParseEngine, beispiel_md: str
) -> None:
    result = engine.pseudonymize(beispiel_md)

    assert "[[PERSON_A1]]" in result.text
    assert "[[EMAIL_A2]]" in result.text
    assert "[[PHONE_A3]]" in result.text


def test_original_values_are_gone_from_the_pseudonymised_text(
    engine: PrivaParseEngine, beispiel_md: str
) -> None:
    result = engine.pseudonymize(beispiel_md)

    for secret in ("Max Mustermann", "max@test.de", "+49 170 1234567"):
        assert secret not in result.text


def test_mapping_is_stored_and_retrievable(
    engine: PrivaParseEngine, beispiel_md: str
) -> None:
    result = engine.pseudonymize(beispiel_md)
    stats = engine.vault_stats()

    assert result.mapping_id
    assert stats.mappings == 1
    assert stats.by_type == {"PERSON": 1, "EMAIL": 1, "PHONE": 1}


def test_restores_the_original_text_exactly(
    engine: PrivaParseEngine, beispiel_md: str
) -> None:
    result = engine.pseudonymize(beispiel_md)
    restored = engine.reverse(result.mapping_id, result.text)

    assert restored.text == beispiel_md
    assert restored.is_clean


def test_every_placeholder_in_an_llm_answer_is_restored(
    engine: PrivaParseEngine, beispiel_md: str
) -> None:
    result = engine.pseudonymize(beispiel_md)
    answer = mock_llm_response(result.text)
    restored = engine.reverse(result.mapping_id, answer)

    assert "[[" not in restored.text
    assert "Max Mustermann" in restored.text
    assert restored.restored > 0
    assert restored.is_clean


def test_repeated_value_reuses_one_placeholder(
    engine: PrivaParseEngine, beispiel_md: str
) -> None:
    """"Max Mustermann" appears twice in the sample and must map to one entity."""
    result = engine.pseudonymize(beispiel_md)

    person_spans = [r for r in result.spans if r.span.type == EntityType.PERSON]
    assert len(person_spans) == 2
    assert len({r.placeholder for r in person_spans}) == 1
    assert result.text.count("[[PERSON_A1]]") == 2
