"""The detector composition layer: the ``Detector`` protocol and ``detect_many``.

Not regex- or backstop-specific — those live in their own test files. This one
is about the plumbing that combines detectors, which every concrete detector
(including the real GLiNER2 one) is assembled through.
"""

from __future__ import annotations

import sys

import pytest

from privaparse.app.config import Settings
from privaparse.app.device import resolve_device
from privaparse.parser.detector import CompositeDetector, StaticDetector, build_default_detector
from privaparse.parser.types import Span


class _DetectOnly:
    """Has ``detect`` and nothing else — GlinerDetector's actual shape.

    ``Detector`` is a structural Protocol, so its ``detect_many`` default body
    reaches no one: nothing inherits from a Protocol. GlinerDetector and every
    hand-written test fake (``NameListDetector`` included) predate
    ``detect_many`` and never got it, so this is what the production hybrid
    detector's first element really looks like.
    """

    def detect(self, text: str) -> list[Span]:
        return [Span(0, len(text), text, "WHOLE")] if text else []


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


def test_static_detector_detect_many_is_a_loop_over_detect():
    detector = StaticDetector([Span(0, 2, "ab", "A")])
    assert detector.detect_many(["ab", "x"]) == [detector.detect("ab"), detector.detect("x")]


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
    assert "pip install -e '.[model]'" in message
    assert "--detector regex" in message
