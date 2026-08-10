"""The evaluation harness itself.

This is the instrument that decides whether GLiNER2 needs fine-tuning. If its
matching or arithmetic is wrong, the decision is wrong, so it gets tested like
production code.
"""

from __future__ import annotations

import pytest

from privaparse.evaluation.build_gold import parse_source
from privaparse.evaluation.harness import (
    PERSON_PRECISION_FLOOR,
    PERSON_RECALL_FLOOR,
    Counts,
    GoldDocument,
    GoldEntity,
    evaluate,
    format_report,
    load_gold,
)
from privaparse.parser.detector import StaticDetector
from privaparse.parser.types import SOURCE_GLINER, EntityType, Span


def _document(text: str, entities: list[tuple[int, int, str]]) -> GoldDocument:
    return GoldDocument(
        id="t-1",
        kind="test",
        text=text,
        entities=tuple(GoldEntity(s, e, t, text[s:e]) for s, e, t in entities),
    )


def _span(text: str, start: int, end: int, entity_type: EntityType) -> Span:
    return Span(
        start=start,
        end=end,
        text=text[start:end],
        type=entity_type,
        score=0.9,
        source=SOURCE_GLINER,
    )


# --- arithmetic ------------------------------------------------------------


def test_counts_arithmetic() -> None:
    counts = Counts(tp=8, fp=2, fn=2)
    assert counts.precision == pytest.approx(0.8)
    assert counts.recall == pytest.approx(0.8)
    assert counts.f1 == pytest.approx(0.8)
    assert counts.support == 10


def test_empty_counts_do_not_divide_by_zero() -> None:
    counts = Counts()
    assert counts.precision == 1.0
    assert counts.recall == 1.0
    assert counts.support == 0


# --- matching --------------------------------------------------------------


def test_perfect_detection_scores_one() -> None:
    text = "Max Mustermann kam."
    doc = _document(text, [(0, 14, "PERSON")])
    report = evaluate(StaticDetector([_span(text, 0, 14, EntityType.PERSON)]), [doc])

    assert report.exact["PERSON"].precision == 1.0
    assert report.exact["PERSON"].recall == 1.0


def test_off_by_one_span_fails_exact_but_passes_partial() -> None:
    """The reason both modes exist: including a title is a boundary
    disagreement, not a miss."""
    text = "Dr. Max Mustermann kam."
    doc = _document(text, [(4, 18, "PERSON")])  # gold excludes "Dr. "
    report = evaluate(StaticDetector([_span(text, 0, 18, EntityType.PERSON)]), [doc])

    assert report.exact["PERSON"].recall == 0.0
    assert report.partial["PERSON"].recall == 1.0


def test_a_missed_entity_is_a_false_negative() -> None:
    text = "Max Mustermann kam."
    doc = _document(text, [(0, 14, "PERSON")])
    report = evaluate(StaticDetector([]), [doc])

    assert report.partial["PERSON"].recall == 0.0
    assert report.partial["PERSON"].fn == 1
    assert [m.text for m in report.false_negatives] == ["Max Mustermann"]


def test_a_spurious_detection_is_a_false_positive() -> None:
    text = "Im Sommer war es warm."
    doc = _document(text, [])
    report = evaluate(StaticDetector([_span(text, 3, 9, EntityType.PERSON)]), [doc])

    assert report.partial["PERSON"].fp == 1
    assert [m.text for m in report.false_positives] == ["Sommer"]


def test_two_predictions_cannot_both_claim_one_gold_entity() -> None:
    """Without one-to-one matching, a detector that emits overlapping guesses
    would score above 100% recall."""
    text = "Max Mustermann kam."
    doc = _document(text, [(0, 14, "PERSON")])
    report = evaluate(
        StaticDetector(
            [_span(text, 0, 14, EntityType.PERSON), _span(text, 0, 3, EntityType.PERSON)]
        ),
        [doc],
    )

    assert report.partial["PERSON"].tp == 1
    assert report.partial["PERSON"].fp == 1
    assert report.partial["PERSON"].recall == 1.0


def test_type_confusion_counts_as_both_a_miss_and_a_false_positive() -> None:
    text = "max@test.de steht da."
    doc = _document(text, [(0, 11, "EMAIL")])
    report = evaluate(StaticDetector([_span(text, 0, 11, EntityType.PERSON)]), [doc])

    assert report.partial["EMAIL"].fn == 1
    assert report.partial["PERSON"].fp == 1


def test_scores_accumulate_across_documents() -> None:
    text = "Max Mustermann kam."
    hit = _document(text, [(0, 14, "PERSON")])
    miss = _document(text, [(0, 14, "PERSON")])
    detector = StaticDetector([_span(text, 0, 14, EntityType.PERSON)])

    report = evaluate(detector, [hit, miss])
    assert report.partial["PERSON"].support == 2
    assert report.partial["PERSON"].recall == 1.0
    assert report.documents == 2


# --- the verdict -----------------------------------------------------------


def _report_with(precision: float, recall: float):
    text = "x"
    report = evaluate(StaticDetector([]), [_document(text, [])], label="stub")
    support = 100
    tp = int(round(recall * support))
    report.partial["PERSON"] = Counts(
        tp=tp,
        fn=support - tp,
        fp=int(round(tp / precision - tp)) if precision else 0,
    )
    return report


def test_verdict_passes_when_both_floors_are_met() -> None:
    report = _report_with(precision=0.90, recall=0.95)
    assert report.needs_finetuning is False
    assert "not required" in report.verdict()


def test_low_recall_alone_triggers_finetuning() -> None:
    report = _report_with(precision=0.99, recall=PERSON_RECALL_FLOOR - 0.05)
    assert report.needs_finetuning is True
    assert "recall" in report.verdict()


def test_low_precision_alone_triggers_finetuning() -> None:
    report = _report_with(precision=PERSON_PRECISION_FLOOR - 0.10, recall=0.99)
    assert report.needs_finetuning is True
    assert "precision" in report.verdict()


def test_report_renders_a_table_and_a_verdict() -> None:
    text = "Max Mustermann kam."
    doc = _document(text, [(0, 14, "PERSON")])
    report = evaluate(StaticDetector([]), [doc], label="stub")

    rendered = format_report([report])
    assert "| Run | Type | Support |" in rendered
    assert "stub" in rendered
    assert "FINE-TUNING WARRANTED" in rendered
    assert "Max Mustermann" in rendered


# --- gold data -------------------------------------------------------------


def test_gold_set_loads_and_every_offset_is_correct() -> None:
    documents = load_gold()
    assert len(documents) >= 30

    for document in documents:
        for entity in document.entities:
            assert document.text[entity.start : entity.end] == entity.text


def test_gold_set_covers_all_three_types_and_includes_negatives() -> None:
    documents = load_gold()
    types = {e.type for d in documents for e in d.entities}
    assert types == {"PERSON", "EMAIL", "PHONE"}

    empty = [d for d in documents if not d.entities]
    assert len(empty) >= 5, "negative documents are how precision gets measured"


def test_gold_set_contains_the_hard_german_cases() -> None:
    joined = "\n".join(d.text for d in load_gold())
    for tricky in ("Müller-Lüdenscheidt", "von Bergen", "Öztürk", "Weiß", "Dipl.-Ing."):
        assert tricky in joined


# --- the gold compiler -----------------------------------------------------


def test_markers_are_stripped_and_offsets_computed() -> None:
    source = "### id: x-1 | kind: test\nHallo {{PERSON:Max Mustermann}}, Mail {{EMAIL:a@b.de}}.\n"
    documents = parse_source(source)

    assert len(documents) == 1
    document = documents[0]
    assert document.text == "Hallo Max Mustermann, Mail a@b.de."
    assert [e["type"] for e in document.entities] == ["PERSON", "EMAIL"]
    for entity in document.entities:
        assert document.text[entity["start"] : entity["end"]] == entity["text"]


def test_text_before_the_first_header_is_ignored() -> None:
    source = "Erklärender Vorspann mit {{PERSON:nichts}}.\n### id: x-1\nEcht.\n"
    documents = parse_source(source)
    assert [d.id for d in documents] == ["x-1"]


def test_overlapping_annotations_are_rejected() -> None:
    """Malformed markers would silently corrupt every score computed from them."""
    from privaparse.evaluation.build_gold import _validate

    with pytest.raises(ValueError, match="overlapping"):
        _validate(
            "x-1",
            "Max Mustermann",
            [
                {"start": 0, "end": 14, "type": "PERSON", "text": "Max Mustermann"},
                {"start": 4, "end": 14, "type": "PERSON", "text": "Mustermann"},
            ],
        )


def test_offset_mismatch_is_rejected() -> None:
    from privaparse.evaluation.build_gold import _validate

    with pytest.raises(ValueError, match="offset mismatch"):
        _validate("x-1", "Max Mustermann", [{"start": 0, "end": 3, "type": "PERSON", "text": "Max Mustermann"}])
