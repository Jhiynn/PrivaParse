"""Batch pseudonymisation: one mapping covering several texts.

The gateway sends one request carrying many text nodes; the model's answer
mixes placeholders from all of them, so they have to share exactly one
mapping for reverse() to restore any of it.
"""

from __future__ import annotations

import pytest

from privaparse.database.models import Entity
from privaparse.database.placeholder import find_placeholders
from privaparse.parser.entity_resolver import UnknownEntityTypeError
from privaparse.parser.types import SOURCE_REGEX, EntityType, Span

TEXTS = [
    "Sehr geehrter Herr Max Mustermann,",
    "Bitte an max@test.de senden.",
    "Max Mustermann ruft unter +49 170 1234567 an.",
]


def test_one_mapping_covers_every_text(engine):
    result = engine.pseudonymize_batch(TEXTS)
    assert len({result.mapping_id}) == 1
    assert len(result.texts) == 3


def test_a_value_in_two_texts_gets_one_placeholder(engine):
    result = engine.pseudonymize_batch(TEXTS)
    first = {m.group(0) for m in find_placeholders(result.texts[0])}
    third = {m.group(0) for m in find_placeholders(result.texts[2])}
    assert first & third


def test_a_value_in_two_texts_gets_one_mapping_entry_with_the_right_count(engine):
    """The placeholder-sharing check above would also pass if two texts, by
    mistake, each got their own MappingEntry for the same entity — the schema
    even forbids that outright (one entry per mapping+entity pair), so that
    particular bug would raise instead of passing quietly. This checks what
    the self-review actually asked for: one entry, occurrences summed across
    every text that used the value.
    """
    result = engine.pseudonymize_batch(TEXTS)

    with engine.database.session() as session:
        repo = engine.repository(session)
        mapping = repo.get_mapping(result.mapping_id)
        person_entries = [e for e in mapping.entries if e.entity.type == "PERSON"]

    assert len(person_entries) == 1
    assert person_entries[0].occurrences == 2


def test_reverse_resolves_placeholders_from_any_text(engine):
    result = engine.pseudonymize_batch(TEXTS)
    for text in result.texts:
        restored = engine.reverse(result.mapping_id, text)
        assert restored.is_clean


def test_batch_refuses_text_that_already_contains_placeholders(engine):
    from privaparse.parser.pseudonymizer import AlreadyPseudonymizedError

    with pytest.raises(AlreadyPseudonymizedError):
        engine.pseudonymize_batch(["fine", "already [[PERSON_A1]] here"])


def test_empty_batch_creates_no_mapping(engine):
    result = engine.pseudonymize_batch([])
    assert result.texts == []
    assert result.replacements == 0


# --- validate every text before writing any of them -------------------------


class _FixedSpansDetector:
    """Hands back a preset span list per text, ignoring content.

    A real detector's output depends on the text; this needs the opposite —
    spans under the test's control, so text 0 can be genuinely valid while
    text 1's type is not. That combination is what actually exercises "nothing
    is written before every text is confirmed": with only one bad text, or the
    bad text first, the guarantee would hold even without a batch-wide check,
    because there would be nothing valid written yet for it to leave behind.
    """

    def __init__(self, per_text: list[list[Span]]) -> None:
        self._per_text = per_text

    def detect(self, text: str) -> list[Span]:  # pragma: no cover - must be unreachable
        raise AssertionError("pseudonymize_batch must call detect_many, not detect")

    def detect_many(self, texts: list[str]) -> list[list[Span]]:
        return self._per_text


def test_an_unknown_type_in_one_text_leaves_no_entity_from_an_earlier_text(repo, settings):
    """The same rule EntityResolver.resolve() already enforces within one text
    — nothing is written until every span in the call has a known type —
    extended across the batch. pseudonymize_batch calls resolve() once per
    text, so without a check spanning the whole batch first, a bad type in
    text 1 would still leave text 0's entity already written by the time text
    1's own call raised.
    """
    from privaparse.parser.pseudonymizer import pseudonymize_batch

    good_text = "Max Mustermann kam."
    bad_text = "abc"
    good_span = Span(0, 14, "Max Mustermann", EntityType.PERSON, 1.0, SOURCE_REGEX)
    bad_span = Span(0, 3, "abc", "NOPE", 1.0, SOURCE_REGEX)
    detector = _FixedSpansDetector([[good_span], [bad_span]])

    with pytest.raises(UnknownEntityTypeError):
        pseudonymize_batch([good_text, bad_text], detector=detector, repo=repo, settings=settings)

    assert repo.session.query(Entity).count() == 0


# --- detection ---------------------------------------------------------


def test_detect_many_matches_detect_one_by_one(fake_detector):
    per_text = fake_detector.detect_many(TEXTS)
    assert per_text == [fake_detector.detect(text) for text in TEXTS]


def test_detect_raw_returns_unfiltered_spans(engine):
    protected, spans = engine.detect_raw(TEXTS[0])
    assert protected.original == TEXTS[0]
    assert isinstance(spans, list)
