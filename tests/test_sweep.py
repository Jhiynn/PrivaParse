from __future__ import annotations

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


def test_format_sweep_produces_one_row_per_threshold():
    from privaparse.parser.types import SOURCE_GLINER, Span

    text = "Max Mustermann schreibt."
    engine = CountingEngine({text: [Span(0, 14, "Max Mustermann", "PERSON", 0.55, SOURCE_GLINER)]})
    results = sweep_thresholds(engine, [_document(text)], thresholds=(0.3, 0.5),
                               catalogue=load_catalogue())

    rendered = format_sweep(results)
    assert "0.30" in rendered and "0.50" in rendered


def test_sweep_scores_each_document_against_its_own_detection():
    """Not in the brief: the three tests above all sweep a single document, so
    none of them ever advances the replay past its first entry. This drives
    two documents with different text and different gold entities through one
    sweep, so a positional off-by-one (the wrong document's spans compared
    against this document's gold) would show up as a wrong count here instead
    of passing silently."""
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
