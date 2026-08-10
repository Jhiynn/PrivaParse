"""GLiNER2 adapter logic — chunking, offset verification, result unpacking.

These run against a stub model, so they need no weights. The tests that need the
real thing are in ``test_gliner_model.py`` behind the ``model`` marker.
"""

from __future__ import annotations

import pytest

from privaparse.app.config import Settings
from privaparse.app.device import resolve_device
from privaparse.parser.gliner_detector import GlinerDetector, chunk_text
from privaparse.parser.types import SOURCE_GLINER, EntityType


class StubModel:
    """Returns a canned result per call, recording how it was invoked."""

    def __init__(self, results: list[dict] | None = None) -> None:
        self.results = results or []
        self.calls: list[tuple[str, tuple, dict]] = []

    def _next(self) -> dict:
        return self.results.pop(0) if self.results else {"entities": {}}

    def extract_entities(self, text, schema, **kwargs):
        self.calls.append(("single", (text,), kwargs))
        return self._next()

    def batch_extract_entities(self, texts, schema, **kwargs):
        self.calls.append(("batch", tuple(texts), kwargs))
        return [self._next() for _ in texts]


@pytest.fixture()
def settings() -> Settings:
    return Settings(device="cpu", warmup=False, chunk_chars=200)


def _detector(settings: Settings, results: list[dict] | None = None) -> GlinerDetector:
    return GlinerDetector(settings, resolve_device(settings), model=StubModel(results))


def _entities(**by_label) -> dict:
    return {"entities": by_label}


# --- unpacking -------------------------------------------------------------


def test_dict_results_become_spans(settings: Settings) -> None:
    text = "Max Mustermann kam."
    detector = _detector(
        settings,
        [_entities(person=[{"text": "Max Mustermann", "start": 0, "end": 14, "confidence": 0.9}])],
    )
    spans = detector.detect(text)

    assert len(spans) == 1
    assert spans[0].type == EntityType.PERSON
    assert spans[0].score == pytest.approx(0.9)
    assert spans[0].source == SOURCE_GLINER
    assert spans[0].verify_against(text)


def test_plain_string_results_are_located_in_the_text(settings: Settings) -> None:
    """The library can return bare strings when spans are not requested."""
    text = "Der Brief von Max Mustermann kam."
    detector = _detector(settings, [_entities(person=["Max Mustermann"])])
    spans = detector.detect(text)

    assert len(spans) == 1
    assert spans[0].verify_against(text)
    assert spans[0].start == text.index("Max Mustermann")


def test_all_three_schema_labels_are_mapped(settings: Settings) -> None:
    text = "Max Mustermann, max@test.de, +49 170 1234567"
    detector = _detector(
        settings,
        [
            _entities(
                person=[{"text": "Max Mustermann", "start": 0, "end": 14, "confidence": 0.9}],
                email=[{"text": "max@test.de", "start": 16, "end": 27, "confidence": 0.9}],
                phone_number=[
                    {"text": "+49 170 1234567", "start": 29, "end": 44, "confidence": 0.9}
                ],
            )
        ],
    )
    assert {s.type for s in detector.detect(text)} == {
        EntityType.PERSON,
        EntityType.EMAIL,
        EntityType.PHONE,
    }


def test_unknown_labels_are_ignored(settings: Settings) -> None:
    text = "Die Muster GmbH in Berlin."
    detector = _detector(
        settings,
        [_entities(organisation=[{"text": "Muster GmbH", "start": 4, "end": 15}])],
    )
    assert detector.detect(text) == []


# --- offset handling -------------------------------------------------------


def test_offsets_that_are_slightly_wrong_are_repaired(settings: Settings) -> None:
    """Tokenizer offsets drift by a character or two; the detection is still
    real, so relocate rather than discard it."""
    text = "Der Brief von Max Mustermann kam."
    detector = _detector(
        settings,
        [_entities(person=[{"text": "Max Mustermann", "start": 12, "end": 26, "confidence": 0.9}])],
    )
    span = detector.detect(text)[0]

    assert span.start == text.index("Max Mustermann")
    assert span.verify_against(text)


def test_a_span_whose_text_is_absent_is_dropped(settings: Settings) -> None:
    """Rewriting on unverifiable offsets would corrupt the document and leak
    the entity that should have been replaced."""
    text = "Max Mustermann kam."
    detector = _detector(
        settings,
        [_entities(person=[{"text": "Erika Musterfrau", "start": 0, "end": 16}])],
    )
    assert detector.detect(text) == []


def test_ambiguous_far_off_offsets_are_dropped(settings: Settings) -> None:
    """If the name occurs many times and the offset points nowhere near any of
    them, there is no safe way to choose."""
    text = "Max kam. " * 40 + "Ende."
    detector = _detector(settings, [_entities(person=[{"text": "Max", "start": 9999}])])
    assert detector.detect(text) == []


def test_empty_text_short_circuits(settings: Settings) -> None:
    detector = _detector(settings)
    assert detector.detect("   \n  ") == []


# --- chunking --------------------------------------------------------------


def test_short_text_is_one_chunk_and_uses_the_single_call(settings: Settings) -> None:
    detector = _detector(settings, [_entities()])
    detector.detect("kurz")
    assert detector._model.calls[0][0] == "single"


def test_long_text_is_batched_in_one_model_call(settings: Settings) -> None:
    text = ("Absatz mit etwas Inhalt.\n\n" * 40).strip()
    detector = _detector(settings, [_entities() for _ in range(20)])
    detector.detect(text)

    kinds = [call[0] for call in detector._model.calls]
    assert kinds == ["batch"], "chunks must go through the model together, not one by one"


def test_long_documents_report_progress(settings: Settings) -> None:
    """A 119 KB document is 375 chunks and over a minute of silence otherwise,
    which is indistinguishable from a hang."""
    text = ("Absatz mit etwas Inhalt.\n\n" * 60).strip()
    seen: list[tuple[int, int]] = []

    detector = GlinerDetector(
        settings,
        resolve_device(settings),
        model=StubModel([_entities() for _ in range(40)]),
        progress=lambda done, total: seen.append((done, total)),
    )
    detector.detect(text)

    assert len(seen) > 1, "progress was reported only once"
    assert seen[-1][0] == seen[-1][1], "final report must show completion"
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)


def test_short_documents_do_not_report_progress(settings: Settings) -> None:
    seen: list[tuple[int, int]] = []
    detector = GlinerDetector(
        settings,
        resolve_device(settings),
        model=StubModel([_entities()]),
        progress=lambda done, total: seen.append((done, total)),
    )
    detector.detect("kurz")
    assert seen == []


def test_batch_call_passes_the_configured_batch_size(settings: Settings) -> None:
    text = ("Absatz mit etwas Inhalt.\n\n" * 40).strip()
    detector = _detector(settings, [_entities() for _ in range(20)])
    detector.detect(text)

    assert detector._model.calls[0][2]["batch_size"] == settings.batch_size


def test_spans_are_returned_in_document_coordinates(settings: Settings) -> None:
    """A chunk's offsets must be translated back before anyone downstream sees
    them."""
    filler = "Ein Satz ohne Namen. " * 15
    text = filler + "Am Ende kam Max Mustermann."
    chunks = chunk_text(text, settings.chunk_chars)
    assert len(chunks) > 1

    results = [_entities() for _ in chunks]
    results[-1] = _entities(person=[{"text": "Max Mustermann", "start": 0, "confidence": 0.9}])

    detector = _detector(settings, results)
    span = detector.detect(text)[0]

    assert span.start == text.index("Max Mustermann")
    assert span.verify_against(text)


def test_duplicate_hits_from_overlapping_chunks_collapse(settings: Settings) -> None:
    text = ("Absatz.\n\n" * 60) + "Max Mustermann."
    chunks = chunk_text(text, settings.chunk_chars)
    index = text.index("Max Mustermann")

    # Every chunk claims the same entity at the same document position.
    results = [
        _entities(person=[{"text": "Max Mustermann", "start": index - c.offset, "confidence": 0.9}])
        for c in chunks
    ]
    detector = _detector(settings, results)

    assert len(detector.detect(text)) == 1


def test_chunks_cover_the_whole_document() -> None:
    text = "Absatz eins.\n\nAbsatz zwei.\n\n" * 30
    chunks = chunk_text(text, 100)

    assert chunks[0].offset == 0
    for chunk in chunks:
        assert text[chunk.offset : chunk.offset + len(chunk.text)] == chunk.text
    assert chunks[-1].offset + len(chunks[-1].text) == len(text)


def test_chunks_overlap_so_boundary_entities_are_seen_whole() -> None:
    text = "x" * 500
    chunks = chunk_text(text, 200, overlap=50)
    for left, right in zip(chunks, chunks[1:]):
        assert right.offset < left.offset + len(left.text)


def test_flash_attention_setting_reaches_the_library(monkeypatch: pytest.MonkeyPatch) -> None:
    """It was declared in Settings and read by nothing for a while — a knob that
    silently does nothing is worse than no knob."""
    import importlib.util

    from privaparse.parser import gliner_detector

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.delenv("USE_FLASHDEBERTA", raising=False)

    gliner_detector._enable_flash_deberta()

    import os

    assert os.environ.get("USE_FLASHDEBERTA") == "1"


def test_flash_attention_without_the_package_warns_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util
    import os

    from privaparse.parser import gliner_detector

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    monkeypatch.delenv("USE_FLASHDEBERTA", raising=False)

    gliner_detector._enable_flash_deberta()

    assert "USE_FLASHDEBERTA" not in os.environ


@pytest.mark.parametrize("max_chars", [200, 256, 300, 384, 512, 768, 1024, 1500, 2048])
def test_chunk_count_stays_proportional_to_document_size(max_chars: int) -> None:
    """Guards the degenerate case: with a fixed 200-char overlap and a small
    window, the loop used to advance one character at a time. A 7.3 KB document
    produced 2374 chunks and took 203 seconds instead of ~30 chunks and ~5."""
    text = ("Ein Absatz mit etwas Inhalt darin. " * 220).strip()  # ~7.5 KB
    chunks = chunk_text(text, max_chars)

    # Even with maximum overlap and the earliest legal split point, a chunk can
    # never cover less than a quarter of its window.
    assert len(chunks) <= (len(text) // (max_chars // 4)) + 2


@pytest.mark.parametrize("max_chars", [200, 256, 384, 512, 1024, 2048])
def test_chunking_always_moves_forward(max_chars: int) -> None:
    text = "Wort " * 2000
    chunks = chunk_text(text, max_chars)
    for left, right in zip(chunks, chunks[1:]):
        assert right.offset > left.offset


@pytest.mark.parametrize("max_chars", [200, 256, 384, 512, 1024, 2048])
def test_chunking_never_leaves_a_gap(max_chars: int) -> None:
    """A gap between two chunks is text nobody scans — a silent miss."""
    text = ("Absatz eins.\n\nAbsatz zwei mit Max Mustermann darin.\n\n" * 60).strip()
    chunks = chunk_text(text, max_chars)

    assert chunks[0].offset == 0
    for left, right in zip(chunks, chunks[1:]):
        assert right.offset <= left.offset + len(left.text)
    assert chunks[-1].offset + len(chunks[-1].text) == len(text)


def test_overlap_is_capped_relative_to_the_window() -> None:
    """An overlap wider than the window cannot be honoured; it must be clamped
    rather than driving the stride negative."""
    text = "x" * 3000
    chunks = chunk_text(text, 200, overlap=500)
    assert len(chunks) < 60
    for left, right in zip(chunks, chunks[1:]):
        assert right.offset > left.offset


def test_chunking_prefers_paragraph_breaks() -> None:
    text = ("A" * 40) + "\n\n" + ("B" * 200)
    chunks = chunk_text(text, 60)
    assert chunks[0].text == "A" * 40 + "\n\n"


def test_an_early_paragraph_break_does_not_produce_a_tiny_chunk() -> None:
    """Splitting at the first break available would waste most of the window
    and multiply the number of model calls."""
    text = "Kurz.\n\n" + ("Langer Absatz. " * 20)
    chunks = chunk_text(text, 60)
    assert len(chunks[0].text) > 30
