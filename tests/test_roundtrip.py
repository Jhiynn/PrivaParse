"""Round-trip properties: what goes in must come back out, byte for byte."""

from __future__ import annotations

import pytest

from privaparse.engine import PrivaParseEngine
from privaparse.parser.pseudonymizer import SpanIntegrityError
from privaparse.parser.types import EntityType

TEXTS = [
    "Hallo Max Mustermann, Mail: max@test.de, Tel: +49 170 1234567.",
    "Kein Kontakt hinterlegt.",
    "",
    "Erika Musterfrau\nErika Musterfrau\nErika Musterfrau",
    "Frau Müller-Lüdenscheidt bittet um Rückruf unter 0170/1234567.",
    "# Titel\n\n- Max Mustermann\n- erika@test.de\n\n> Zitat von Max Mustermann\n",
]


@pytest.mark.parametrize("text", TEXTS)
def test_reverse_undoes_pseudonymize(engine: PrivaParseEngine, text: str) -> None:
    result = engine.pseudonymize(text)
    restored = engine.reverse(result.mapping_id, result.text)
    assert restored.text == text


def test_markdown_structure_survives_unchanged(
    engine: PrivaParseEngine, mit_code_md: str
) -> None:
    result = engine.pseudonymize(mit_code_md)
    restored = engine.reverse(result.mapping_id, result.text)

    assert restored.text == mit_code_md
    # Line count and fence markers are untouched, because only character spans
    # are ever replaced.
    assert result.text.count("\n") == mit_code_md.count("\n")
    assert result.text.count("```") == mit_code_md.count("```")
    assert "| Feld | Wert |" in result.text


def test_pii_inside_a_code_fence_is_left_alone(
    engine: PrivaParseEngine, mit_code_md: str
) -> None:
    result = engine.pseudonymize(mit_code_md)

    assert 'DEFAULT_USER = "max@test.de"' in result.text
    assert 'SUPPORT_PHONE = "+49 170 1234567"' in result.text
    # ...while the same address in prose is replaced.
    assert "erreichbar unter max@test.de" not in result.text


def test_inline_code_and_urls_are_left_alone(
    engine: PrivaParseEngine, mit_code_md: str
) -> None:
    result = engine.pseudonymize(mit_code_md)

    assert "Der Wert `max@test.de` ist" in result.text
    assert "https://mueller-partner.de/doku" in result.text


def test_mailto_target_is_pseudonymised(engine: PrivaParseEngine, mit_code_md: str) -> None:
    """A mailto link is a real address, not a URL to be protected."""
    result = engine.pseudonymize(mit_code_md)
    assert "erika@test.de" not in result.text.split("```")[0] + result.text.split("```")[-1]
    assert "mailto:[[EMAIL_" in result.text


def test_frontmatter_author_is_pseudonymised(
    engine: PrivaParseEngine, mit_code_md: str
) -> None:
    result = engine.pseudonymize(mit_code_md)
    assert "author: [[PERSON_" in result.text


def test_placeholders_are_stable_across_documents(engine: PrivaParseEngine) -> None:
    """The point of a global vault."""
    first = engine.pseudonymize("Max Mustermann schrieb.", source_name="a.md")
    second = engine.pseudonymize("Erneut von Max Mustermann.", source_name="b.md")

    placeholder = first.spans[0].placeholder
    assert second.spans[0].placeholder == placeholder
    assert engine.vault_stats().entities == 1


def test_each_document_gets_its_own_spelling_back(engine: PrivaParseEngine) -> None:
    """Both spellings map to one entity, but restoration is per document."""
    upper = engine.pseudonymize("MAX MUSTERMANN kam.", source_name="a.md")
    title = engine.pseudonymize("Max Mustermann kam.", source_name="b.md")

    assert upper.spans[0].placeholder == title.spans[0].placeholder
    assert engine.reverse(upper.mapping_id, upper.text).text == "MAX MUSTERMANN kam."
    assert engine.reverse(title.mapping_id, title.text).text == "Max Mustermann kam."


def test_repeated_pseudonymisation_of_the_same_text_is_deterministic(
    engine: PrivaParseEngine, beispiel_md: str
) -> None:
    first = engine.pseudonymize(beispiel_md)
    second = engine.pseudonymize(beispiel_md)

    assert first.text == second.text
    assert first.mapping_id != second.mapping_id  # separate mappings


def test_corrupt_span_offsets_abort_instead_of_damaging_the_document(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rewriting on bad offsets would both mangle the text and leak the entity
    that should have been replaced. It must fail loudly."""
    from privaparse.parser.detector import StaticDetector
    from privaparse.parser.types import SOURCE_GLINER, Span

    text = "Max Mustermann kam."
    wrong = Span(
        start=0, end=14, text="Erika Musterfr", type=EntityType.PERSON,
        score=0.9, source=SOURCE_GLINER,
    )
    engine = PrivaParseEngine(
        settings, detector=StaticDetector([wrong]), configure_logs=False
    )
    try:
        with pytest.raises(SpanIntegrityError):
            engine.pseudonymize(text)
    finally:
        engine.close()
