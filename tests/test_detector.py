"""The detector composition layer: the ``Detector`` protocol and ``detect_many``.

Not regex- or backstop-specific — those live in their own test files. This one
is about the plumbing that combines detectors, which every concrete detector
(including the real GLiNER2 one) is assembled through.
"""

from __future__ import annotations

from privaparse.parser.detector import CompositeDetector, StaticDetector
from privaparse.parser.types import Span


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
