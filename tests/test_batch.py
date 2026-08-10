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
from privaparse.parser.types import SOURCE_GLINER, SOURCE_REGEX, EntityType, Span

TEXTS = [
    "Sehr geehrter Herr Max Mustermann,",
    "Bitte an max@test.de senden.",
    "Max Mustermann ruft unter +49 170 1234567 an.",
]


def test_one_mapping_covers_every_text(engine):
    """One id has to be enough to restore placeholders drawn from all three
    texts at once — the actual scenario this task exists for: an LLM answer
    that mixes placeholders the model copied from several of the batch's
    texts into one response.
    """
    result = engine.pseudonymize_batch(TEXTS)
    assert len(result.texts) == 3

    combined_answer = "\n".join(result.texts)
    restored = engine.reverse(result.mapping_id, combined_answer)
    assert restored.is_clean


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


def test_empty_batch_creates_a_real_mapping_with_zero_entries(engine):
    """Ruled by the project owner: a falsy mapping_id (empty string, or None)
    is a trap specifically because ``PrivaParseEngine.reverse`` treats a
    falsy id as "find whichever session issued every placeholder in this
    text" rather than "this exact session, and nothing else". A caller handed
    a falsy id from an empty batch and then calling
    ``engine.reverse(empty.mapping_id, answer)`` would silently resolve
    against some *other* session that happens to cover the text — reproduced
    below with a placeholder from a real, unrelated batch. A real mapping id
    that happens to have issued nothing behaves like every other mapping id
    instead: lookup is explicit, not a fallback search, and correctly
    resolves nothing.
    """
    other = engine.pseudonymize_batch(TEXTS)
    empty = engine.pseudonymize_batch([])

    assert empty.mapping_id
    assert empty.texts == []
    assert empty.replacements == 0

    with engine.database.session() as session:
        mapping = engine.repository(session).get_mapping(empty.mapping_id)
        assert mapping is not None
        assert mapping.entries == []

    foreign_placeholder = other.spans[0][0].placeholder
    result = engine.reverse(empty.mapping_id, f"Re: {foreign_placeholder}")
    assert result.restored == 0
    assert result.foreign == [foreign_placeholder]
    assert foreign_placeholder in result.text


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


def test_detect_raw_returns_unfiltered_spans(settings):
    """``isinstance(spans, list)`` would pass even if detect_raw quietly ran
    the merge/threshold step and returned an empty or fully-filtered list —
    it does not test the one thing detect_raw promises over detect(): that
    nothing has been dropped yet. A span scored below the merge threshold is
    the direct way to show that: detect_raw must still return it, and
    detect() — which does run resolve_spans — must not.
    """
    from privaparse.engine import PrivaParseEngine
    from privaparse.parser.detector import StaticDetector

    text = "Vielleicht Max Mustermann, vielleicht nicht."
    start = text.index("Max Mustermann")
    weak = Span(
        start,
        start + len("Max Mustermann"),
        "Max Mustermann",
        EntityType.PERSON,
        0.1,
        SOURCE_GLINER,
    )
    assert weak.score < settings.threshold  # otherwise this test proves nothing

    engine = PrivaParseEngine(settings, detector=StaticDetector([weak]), configure_logs=False)
    try:
        protected, raw_spans = engine.detect_raw(text)
        resolved_spans = engine.detect(text)

        assert protected.original == text
        assert weak in raw_spans
        assert weak not in resolved_spans
    finally:
        engine.close()
