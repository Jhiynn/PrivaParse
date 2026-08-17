from __future__ import annotations

import dataclasses

from privaparse.app.catalogue import load_catalogue
from privaparse.evaluation.harness import GoldDocument, format_sweep, sweep_thresholds


class CountingEngine:
    """Records how often the expensive pass runs."""

    def __init__(self, spans_by_text):
        self.spans_by_text = spans_by_text
        self.calls = 0
        self.settings = type("S", (), {"scan_code": False, "coreference_sweep": False})()

    def detect_raw(self, text):
        from privaparse.parser.markdown import protect

        self.calls += 1
        return protect(text), list(self.spans_by_text.get(text, []))

    @property
    def catalogue(self):
        return load_catalogue()


def _document(text, entities=()):
    return GoldDocument("d1", "notiz", text, tuple(entities))


def test_sweep_runs_the_model_once_per_document():
    from privaparse.parser.types import SOURCE_GLINER, Span

    text = "Max Mustermann schreibt."
    engine = CountingEngine({text: [Span(0, 14, "Max Mustermann", "PERSON", 0.55, SOURCE_GLINER)]})

    sweep_thresholds(engine, [_document(text)], thresholds=(0.3, 0.5, 0.7, 0.9),
                     catalogue=load_catalogue())
    assert engine.calls == 1


def test_raising_the_threshold_drops_low_scoring_spans():
    from privaparse.evaluation.harness import GoldEntity
    from privaparse.parser.types import SOURCE_GLINER, Span

    text = "Max Mustermann schreibt."
    gold = [GoldEntity(0, 14, "PERSON", "Max Mustermann")]
    engine = CountingEngine({text: [Span(0, 14, "Max Mustermann", "PERSON", 0.55, SOURCE_GLINER)]})

    results = sweep_thresholds(engine, [_document(text, gold)], thresholds=(0.5, 0.9),
                               catalogue=load_catalogue())
    assert results[0.5].partial["PERSON"].recall == 1.0
    assert results[0.9].partial["PERSON"].recall == 0.0


def test_sweep_still_varies_for_a_type_that_declares_its_own_threshold():
    """The reason ``sweep_thresholds`` sweeps against a stripped catalogue
    (``_without_per_type_thresholds``), not the caller's own: ``merge_spans``
    now consults a type's catalogue threshold ahead of whatever it is
    called with (the fix that gave ``Catalogue.threshold_for`` a caller at
    all). Left unstripped, a type that already declares a threshold would
    keep filtering at that one fixed value throughout the sweep — its curve
    would go flat at exactly the types the sweep most needs to inform,
    which is the silent failure this test exists to catch.

    PERSON is given an explicit catalogue threshold of 0.6, deliberately
    sitting strictly between the two swept values (0.5, 0.9) and strictly
    above the span's own score (0.55). If that 0.6 leaked into the sweep
    unstripped, it would reject the span at *both* swept points alike —
    recall 0.0 at 0.5 and 0.0 at 0.9, a flat line. Stripped correctly, the
    swept value alone decides, exactly as it does for a type with no
    catalogue threshold at all (see test_raising_the_threshold_drops_low_scoring_spans,
    same span, same thresholds, no per-type override): recall 1.0 at 0.5,
    0.0 at 0.9 — a curve that actually moves.
    """
    from privaparse.evaluation.harness import GoldEntity
    from privaparse.parser.types import SOURCE_GLINER, Span

    text = "Max Mustermann schreibt."
    gold = [GoldEntity(0, 14, "PERSON", "Max Mustermann")]
    engine = CountingEngine({text: [Span(0, 14, "Max Mustermann", "PERSON", 0.55, SOURCE_GLINER)]})

    base = load_catalogue()
    strict_person = dataclasses.replace(base.get("PERSON"), threshold=0.6)
    catalogue = dataclasses.replace(base, types={**base.types, "PERSON": strict_person})
    assert catalogue.get("PERSON").threshold == 0.6  # the case this test relies on

    results = sweep_thresholds(
        engine, [_document(text, gold)], thresholds=(0.5, 0.9), catalogue=catalogue
    )
    assert results[0.5].partial["PERSON"].recall == 1.0
    assert results[0.9].partial["PERSON"].recall == 0.0


def test_format_sweep_produces_one_row_per_threshold():
    from privaparse.parser.types import SOURCE_GLINER, Span

    text = "Max Mustermann schreibt."
    engine = CountingEngine({text: [Span(0, 14, "Max Mustermann", "PERSON", 0.55, SOURCE_GLINER)]})
    results = sweep_thresholds(engine, [_document(text)], thresholds=(0.3, 0.5),
                               catalogue=load_catalogue())

    rendered = format_sweep(results)
    assert "0.30" in rendered and "0.50" in rendered


def test_sweep_scores_each_document_against_its_own_detection():
    """Extra coverage, added after the fact: the three tests above all sweep a single document, so
    none of them ever gets past the first entry of the sweep's scanned list.
    This drives two documents with different text and different gold entities
    through one sweep, so a positional off-by-one (the wrong document's spans
    compared against this document's gold) would show up as a wrong count here
    instead of passing silently."""
    from privaparse.evaluation.harness import GoldEntity
    from privaparse.parser.types import SOURCE_GLINER, Span

    hit_text = "Max Mustermann schreibt."
    miss_text = "Erika Musterfrau ruft an."
    hit = GoldDocument("d1", "notiz", hit_text, (GoldEntity(0, 14, "PERSON", "Max Mustermann"),))
    miss = GoldDocument(
        "d2", "notiz", miss_text, (GoldEntity(0, 16, "PERSON", "Erika Musterfrau"),)
    )
    # miss_text has no entry at all: detect_raw legitimately returns nothing
    # for it, same as a real document the model found no PII in.
    engine = CountingEngine(
        {hit_text: [Span(0, 14, "Max Mustermann", "PERSON", 0.55, SOURCE_GLINER)]}
    )

    results = sweep_thresholds(engine, [hit, miss], thresholds=(0.5,), catalogue=load_catalogue())

    assert engine.calls == 2
    assert results[0.5].partial["PERSON"].tp == 1  # hit, matched
    assert results[0.5].partial["PERSON"].fn == 1  # miss, correctly unmatched


def test_sweep_re_merges_instead_of_filtering_a_cached_merge():
    """Pins the sweep's central claim: every point on the curve is a real
    re-merge of the raw spans, not a score filter over one cached merge.
    Every other test in this file uses a single, non-competing span, so none
    of them would fail if the sweep were "simplified" into merging once and
    filtering the cached result by score instead — that anti-pattern
    is invisible unless two spans of different types actually compete for an
    overlap at one threshold and stop competing at another.

    Two overlapping GLiNER spans, same start: PERSON is short and strong
    (0.75), ADDRESS is long and weaker (0.55). The merge's greedy sort puts
    length first, so at 0.5 both clear the score floor and ADDRESS's extra
    length wins the overlap outright, burying PERSON entirely — recall 0.0.
    At 0.6 ADDRESS itself drops below the floor before any overlap is ever
    decided, leaving PERSON to stand alone — recall 1.0.

    A filter-the-merged-output implementation cannot reproduce that flip:
    once PERSON loses the one merge it ever runs, no later score filter can
    resurrect it, so every threshold from 0.6 up would show PERSON recall
    stuck at 0.0 rather than 1.0 — checked directly against such an
    implementation before this test was added.
    """
    from privaparse.evaluation.harness import GoldEntity
    from privaparse.parser.types import SOURCE_GLINER, Span

    text = "Max Mustermann wohnt in der Musterstrasse 5"
    person_end = len("Max Mustermann")
    address_end = len("Max Mustermann wohnt in der Musterstrasse")
    gold = [GoldEntity(0, person_end, "PERSON", text[:person_end])]
    spans = [
        Span(0, person_end, text[:person_end], "PERSON", 0.75, SOURCE_GLINER),
        Span(0, address_end, text[:address_end], "ADDRESS", 0.55, SOURCE_GLINER),
    ]
    engine = CountingEngine({text: spans})

    results = sweep_thresholds(
        engine, [_document(text, gold)], thresholds=(0.5, 0.6), catalogue=load_catalogue()
    )

    assert results[0.5].partial["PERSON"].recall == 0.0  # buried by the longer ADDRESS span
    assert results[0.6].partial["PERSON"].recall == 1.0  # ADDRESS now below the floor


class _CallOrderSensitiveEngine:
    """Returns a different span list on each successive call, even for
    identical text. Legal under `SupportsDetectRaw`, which promises nothing
    about purity — it only requires `detect_raw(text) -> (ProtectedText,
    list[Span])`. Exists to prove the sweep keeps each document's scan
    genuinely positional rather than incidentally so: anything keyed by text
    cannot tell two scans of the same text apart and would let the second
    stand in for the first."""

    def __init__(self, spans_by_call):
        self.spans_by_call = list(spans_by_call)
        self.calls = 0
        self.settings = type("S", (), {"scan_code": False, "coreference_sweep": False})()

    def detect_raw(self, text):
        from privaparse.parser.markdown import protect

        spans = self.spans_by_call[self.calls]
        self.calls += 1
        return protect(text), list(spans)


def test_the_sweep_is_positional_not_text_keyed():
    """Pins how the sweep finds a document's scan, with the one case that tells
    positional and text-keyed caching apart: two documents sharing identical
    text, whose own `detect_raw()` calls return *different* results. The
    first document's call finds the name; the second's call — same text, a
    later moment — legally finds nothing (`SupportsDetectRaw` promises
    nothing about purity between calls).

    A cache keyed by text has exactly one slot for this text, so the second
    (empty) result overwrites the first there. Both documents would then
    replay as "nothing found", and the first document's real, already-found
    match would be lost — PERSON recall would read 0.0 with tp=0, fn=1
    instead of the correct tp=1, fn=0. Position-keyed replay does not
    collapse the two, because each document's own call is found by *when*
    it happened, never by what text it happened to share with another
    document. Checked directly against a text-keyed cache before this test
    was added: it reproduces exactly that tp=0, fn=1 misattribution."""
    from privaparse.evaluation.harness import GoldEntity
    from privaparse.parser.types import SOURCE_GLINER, Span

    text = "Max Mustermann schreibt."
    span = Span(0, 14, "Max Mustermann", "PERSON", 0.9, SOURCE_GLINER)
    engine = _CallOrderSensitiveEngine([[span], []])

    first = GoldDocument("d1", "notiz", text, (GoldEntity(0, 14, "PERSON", "Max Mustermann"),))
    second = GoldDocument("d2", "notiz", text, ())

    results = sweep_thresholds(
        engine, [first, second], thresholds=(0.5,), catalogue=load_catalogue()
    )

    counts = results[0.5].partial["PERSON"]
    assert counts.tp == 1  # first: matched against its own call's detection
    assert counts.fn == 0
    assert counts.fp == 0
