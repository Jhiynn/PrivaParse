"""The detector composition layer: the ``Detector`` protocol and ``detect_batch``.

Not regex- or backstop-specific — those live in their own test files. This one
is about the plumbing that combines detectors, which every concrete detector
(including the real GLiNER2 one) is assembled through.
"""

from __future__ import annotations

import sys

import pytest

from privaparse.app.config import Settings
from privaparse.app.device import resolve_device
from privaparse.parser.detector import (
    CompositeDetector,
    Detector,
    StaticDetector,
    build_default_detector,
    detect_batch,
)
from privaparse.parser.types import Span


class _DetectOnly:
    """Has ``detect`` and nothing else — GlinerDetector's actual shape.

    ``Detector`` is a structural Protocol, so a detector satisfies it by having
    ``detect`` and inheriting from nothing. Most of them — GlinerDetector, and
    every hand-written test fake, ``NameListDetector`` included — stop there and
    never define a batch form, so this is what the production hybrid detector's
    first element really looks like.
    """

    def detect(self, text: str) -> list[Span]:
        return [Span(0, len(text), text, "WHOLE")] if text else []


class _Batching:
    """Defines both forms, and records which one was asked."""

    def __init__(self) -> None:
        self.batched: list[list[str]] = []
        self.singles: list[str] = []

    def detect(self, text: str) -> list[Span]:
        self.singles.append(text)
        return [Span(0, len(text), text, "SINGLE")] if text else []

    def detect_many(self, texts) -> list[list[Span]]:
        self.batched.append(list(texts))
        return [[Span(0, len(t), t, "BATCH")] if t else [] for t in texts]


# --- detect_batch: the one place that decides between batching and looping ---


def test_detect_batch_loops_over_detect_when_the_detector_does_not_batch():
    detector = _DetectOnly()

    results = detect_batch(detector, ["ab", "xyz"])

    assert [[s.text for s in spans] for spans in results] == [["ab"], ["xyz"]]


def test_detect_batch_hands_the_whole_sequence_to_a_detector_that_batches():
    """One submission, not one per text — the point of a detector defining
    ``detect_many`` at all is that the model call gets to see every text at
    once, and a helper that looped would silently throw that away."""
    detector = _Batching()

    results = detect_batch(detector, ["ab", "xyz"])

    assert detector.batched == [["ab", "xyz"]]
    assert detector.singles == []
    assert [[s.type for s in spans] for spans in results] == [["BATCH"], ["BATCH"]]


def test_detect_batch_over_the_fixed_span_detector_matches_detect_per_text():
    detector = StaticDetector([Span(0, 2, "ab", "A")])
    assert detect_batch(detector, ["ab", "x"]) == [detector.detect("ab"), detector.detect("x")]


def test_a_detect_only_fake_satisfies_the_detector_protocol():
    """Load-bearing: the protocol stays structural and ``runtime_checkable``,
    and declares no batch form. Declaring one would make every detect-only
    detector — the real GLiNER2 one included — fail this check.
    """
    assert isinstance(_DetectOnly(), Detector)


def test_composite_detect_many_tolerates_a_detector_without_it():
    composite = CompositeDetector([_DetectOnly()])

    results = composite.detect_many(["ab", "xyz"])

    assert [s.text for s in results[0]] == ["ab"]
    assert [s.text for s in results[1]] == ["xyz"]


def test_composite_detect_many_keeps_each_texts_spans_separate():
    """Fan out per text and merge per text — never flatten across texts.

    Each ``StaticDetector`` here only keeps a span if it fits inside the text
    being scanned, so a short and a long text make the per-text alignment
    observable: get the merge wrong (e.g. concatenate every detector's spans
    into one pool) and the short text's result silently picks up a span that
    only ever fit the long one.
    """
    short_text = "ab"
    long_text = "abcdefghij"

    fits_both = StaticDetector([Span(0, 2, "ab", "A")])
    fits_long_only = StaticDetector([Span(0, 8, "abcdefgh", "B")])
    composite = CompositeDetector([fits_both, fits_long_only])

    results = composite.detect_many([short_text, long_text])

    assert [s.type for s in results[0]] == ["A"]
    assert sorted(s.type for s in results[1]) == ["A", "B"]


def test_composite_detect_many_matches_detect_called_separately():
    text_a = "hello world"
    text_b = "goodbye"
    detector = CompositeDetector([StaticDetector([Span(0, 5, "hello", "A")])])

    batched = detector.detect_many([text_a, text_b])

    assert batched == [detector.detect(text_a), detector.detect(text_b)]


def test_the_shared_fake_detector_batches_the_same_way_it_detects(fake_detector):
    """The same claim as above, but for the composite the rest of the suite
    actually runs on -- the model half replaced by a known-name matcher, the
    regex half real. Every pipeline test that compares a batched result with a
    single-text one rests on this fixture batching faithfully.
    """
    texts = ["Erika Musterfrau schrieb.", "Antwort an max@test.de.", "nichts hier"]

    assert fake_detector.detect_many(texts) == [fake_detector.detect(text) for text in texts]


# --- the GLiNER2-absent guard -----------------------------------------------


@pytest.mark.parametrize("mode", ["hybrid", "gliner"])
def test_missing_gliner2_raises_a_friendly_runtime_error(monkeypatch, mode) -> None:
    """The user-visible contract: build a detector without the model backend
    installed, get a ``RuntimeError`` with install guidance -- not a raw
    ``ModuleNotFoundError`` with no explanation.

    ``gliner2`` is actually installed on the machine running this suite (it is
    what makes ``pytest -m model`` possible), so its absence has to be
    simulated rather than uninstalled. Setting the ``sys.modules`` entry to
    ``None`` makes the import machinery treat the name as blocked and raise
    ``ModuleNotFoundError`` on the next ``import gliner2`` -- exactly what a
    real "not installed" environment produces -- and ``monkeypatch`` restores
    whatever was there afterwards.
    """
    monkeypatch.setitem(sys.modules, "gliner2", None)
    settings = Settings(detector=mode, device="cpu", warmup=False)

    with pytest.raises(RuntimeError) as excinfo:
        build_default_detector(settings, resolve_device(settings))

    message = str(excinfo.value)
    assert "pipx inject privaparse" in message
    assert "pip install -e '.[model]'" in message
    assert "--detector regex" in message
