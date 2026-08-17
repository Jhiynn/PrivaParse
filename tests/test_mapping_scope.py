"""The security boundary of a global vault.

Placeholders are stable across documents, which means they are guessable. If
``reverse()`` resolved any placeholder the vault knows, then writing
``[[PERSON_A47]]`` into a document would read back the name of a person the
caller has never seen. So resolution is scoped to the mapping that issued the
placeholder — nothing else.
"""

from __future__ import annotations

import pytest

from privaparse.app.config import Settings
from privaparse.engine import PrivaParseEngine
from privaparse.parser.reverse_mapper import (
    ForeignPlaceholderError,
    NoCoveringMappingError,
    UnknownMappingError,
)


@pytest.fixture()
def two_documents(engine: PrivaParseEngine):
    doc_a = engine.pseudonymize("Max Mustermann kam.", source_name="a.md")
    doc_b = engine.pseudonymize("Erika Musterfrau kam.", source_name="b.md")
    return doc_a, doc_b


def test_a_foreign_placeholder_is_not_resolved(
    engine: PrivaParseEngine, two_documents
) -> None:
    doc_a, doc_b = two_documents
    foreign = doc_b.spans[0].placeholder

    result = engine.reverse(doc_a.mapping_id, f"Antwort zu {foreign}.")

    assert foreign in result.text
    assert "Erika Musterfrau" not in result.text
    assert result.foreign == [foreign]
    assert result.restored == 0


def test_own_placeholders_still_resolve_alongside_a_foreign_one(
    engine: PrivaParseEngine, two_documents
) -> None:
    doc_a, doc_b = two_documents
    mine = doc_a.spans[0].placeholder
    theirs = doc_b.spans[0].placeholder

    result = engine.reverse(doc_a.mapping_id, f"{mine} und {theirs}")

    assert "Max Mustermann" in result.text
    assert theirs in result.text
    assert result.restored == 1
    assert result.foreign == [theirs]


def test_strict_mode_raises_on_a_foreign_placeholder(
    engine: PrivaParseEngine, two_documents
) -> None:
    doc_a, doc_b = two_documents
    theirs = doc_b.spans[0].placeholder

    with pytest.raises(ForeignPlaceholderError):
        engine.reverse(doc_a.mapping_id, f"Antwort zu {theirs}.", strict=True)


def test_an_invented_placeholder_is_reported_separately(
    engine: PrivaParseEngine, two_documents
) -> None:
    """Distinguishing "belongs to someone else" from "never existed" matters:
    the first is an access attempt, the second is a hallucinating model."""
    doc_a, _ = two_documents

    result = engine.reverse(doc_a.mapping_id, "Grüße an [[PERSON_ZZ9]].")

    assert "[[PERSON_ZZ9]]" in result.text
    assert result.unknown == ["[[PERSON_ZZ9]]"]
    assert result.foreign == []


def test_strict_mode_tolerates_invented_placeholders(
    engine: PrivaParseEngine, two_documents
) -> None:
    """Nothing leaks, so there is nothing to abort for — it is only reported."""
    doc_a, _ = two_documents
    result = engine.reverse(doc_a.mapping_id, "Grüße an [[PERSON_ZZ9]].", strict=True)
    assert result.unknown == ["[[PERSON_ZZ9]]"]


# --- irreversible placeholders ---------------------------------------------

#: The card backstop finds this, and CARD is irreversible in the default
#: catalogue: nothing readable about it is ever written to the vault.
CARD_TEXT = "Zahlung mit Karte 4111 1111 1111 1111 am Montag."

CARD_MADE_REVERSIBLE = """
version: 1
placeholder_types:
  CARD:
    reversible: true
"""


@pytest.fixture()
def card_document(engine: PrivaParseEngine):
    return engine.pseudonymize(CARD_TEXT, source_name="karte.md")


def test_an_irreversible_placeholder_is_recognised_not_attributed(
    engine: PrivaParseEngine, card_document
) -> None:
    """Its own document issued it, but no mapping entry records that — so the
    outcome says "no way home", not "someone else's"."""
    card = card_document.spans[0].placeholder

    result = engine.reverse(card_document.mapping_id, card_document.text)

    assert result.irreversible == [card]
    assert result.foreign == []
    assert result.unknown == []
    assert card in result.text
    assert result.restored == 0


def test_a_document_with_only_irreversible_leftovers_is_clean(
    engine: PrivaParseEngine, card_document
) -> None:
    """`is_clean` means "nothing here surprised me", and this does not."""
    result = engine.reverse(card_document.mapping_id, card_document.text)
    assert result.is_clean


def test_an_invented_placeholder_beside_an_irreversible_one_is_still_unknown(
    engine: PrivaParseEngine, card_document
) -> None:
    """The irreversible question is asked first, so it is the one that could
    swallow a placeholder the vault never issued. It must not."""
    text = f"{card_document.text}\n\nGrüße an [[PERSON_ZZ9]]."

    result = engine.reverse(card_document.mapping_id, text)

    assert result.unknown == ["[[PERSON_ZZ9]]"]
    assert result.irreversible == [card_document.spans[0].placeholder]


def test_strict_mode_does_not_raise_on_an_irreversible_placeholder(
    engine: PrivaParseEngine, card_document
) -> None:
    result = engine.reverse(card_document.mapping_id, card_document.text, strict=True)
    assert result.irreversible == [card_document.spans[0].placeholder]


def test_strict_mode_still_raises_beside_an_irreversible_placeholder(
    engine: PrivaParseEngine, two_documents, card_document
) -> None:
    """The protection strict mode exists for is untouched: a placeholder
    genuinely issued to another mapping still aborts the reversal."""
    _, doc_b = two_documents
    text = f"{card_document.text}\n\nUnd noch {doc_b.spans[0].placeholder}."

    with pytest.raises(ForeignPlaceholderError):
        engine.reverse(card_document.mapping_id, text, strict=True)


def test_reversible_placeholders_still_restore_beside_an_irreversible_one(
    engine: PrivaParseEngine,
) -> None:
    doc = engine.pseudonymize(f"Max Mustermann zahlt. {CARD_TEXT}", source_name="k.md")
    card = next(s.placeholder for s in doc.spans if s.span.type == "CARD")

    result = engine.reverse(doc.mapping_id, doc.text)

    assert "Max Mustermann" in result.text
    assert result.restored == 1
    assert result.irreversible == [card]
    assert result.is_clean


def test_classification_ignores_the_catalogues_reversible_flag(
    settings, fake_detector, tmp_path
) -> None:
    """A catalogue edit must not re-classify placeholders issued before it.

    The vault holds no surface form for this card and never will, whatever the
    flag says today — so reversal keeps reporting it as irreversible.
    """
    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    document = engine.pseudonymize(CARD_TEXT, source_name="karte.md")
    engine.close()

    edited = tmp_path / "privaparse.entities.yaml"
    edited.write_text(CARD_MADE_REVERSIBLE, encoding="utf-8")
    after = PrivaParseEngine(
        Settings(db_path=settings.db_path, device="cpu", catalogue_path=edited),
        detector=fake_detector,
        configure_logs=False,
    )
    try:
        assert after.catalogue.get("CARD").reversible
        result = after.reverse(document.mapping_id, document.text)
    finally:
        after.close()

    assert result.irreversible == [document.spans[0].placeholder]
    assert result.foreign == []
    assert result.restored == 0


def test_unknown_mapping_id_is_rejected(engine: PrivaParseEngine) -> None:
    with pytest.raises(UnknownMappingError):
        engine.reverse("00000000-0000-0000-0000-000000000000", "[[PERSON_A1]]")


def test_reverse_reports_a_clean_run(engine: PrivaParseEngine, two_documents) -> None:
    doc_a, _ = two_documents
    result = engine.reverse(doc_a.mapping_id, doc_a.text)
    assert result.is_clean


# --- automatic mapping lookup ----------------------------------------------


def test_omitting_the_mapping_finds_the_right_mapping(
    engine: PrivaParseEngine, two_documents
) -> None:
    """Managing ids by hand is friction, not a security property."""
    doc_a, _ = two_documents
    result = engine.reverse(None, doc_a.text)

    assert "Max Mustermann" in result.text
    assert result.is_clean


def test_automatic_lookup_does_not_unmask_someone_elses_placeholder(
    engine: PrivaParseEngine, two_documents
) -> None:
    """The point of the whole mapping mechanism.

    A crafted file mixing in a foreign placeholder is covered by no mapping, so
    the lookup refuses rather than quietly picking whichever mapping happens to
    know that placeholder.
    """
    doc_a, doc_b = two_documents
    crafted = f"{doc_a.text}\n\nUnd noch {doc_b.spans[0].placeholder}."

    with pytest.raises(NoCoveringMappingError):
        engine.reverse(None, crafted)


def test_automatic_lookup_refuses_an_invented_placeholder(
    engine: PrivaParseEngine, two_documents
) -> None:
    with pytest.raises(NoCoveringMappingError) as excinfo:
        engine.reverse(None, "Grüße an [[PERSON_ZZ9]].")
    assert "never issued by this vault" in str(excinfo.value)


def test_automatic_lookup_needs_something_to_look_up(engine: PrivaParseEngine) -> None:
    with pytest.raises(NoCoveringMappingError, match="no placeholders"):
        engine.reverse(None, "Ein Text ganz ohne Platzhalter.")


def test_automatic_lookup_prefers_the_newest_covering_mapping(
    engine: PrivaParseEngine,
) -> None:
    """Re-running pseudonymize on the same document is common; the newest
    mapping covering everything is the one the caller almost certainly means."""
    text = "Max Mustermann kam."
    first = engine.pseudonymize(text, source_name="a.md")
    second = engine.pseudonymize(text, source_name="a.md")

    from privaparse.parser.reverse_mapper import find_mapping_for

    with engine.database.session() as session:
        found = find_mapping_for(first.text, repo=engine.repository(session))

    assert found == second.mapping_id
    assert found != first.mapping_id
