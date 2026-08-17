"""The detection pass: masking, the detector, the threshold, merging, the sweep.

Every test here builds the pass from a fake detector and a real catalogue. None
of them touches the vault — the pass is read-only by construction, and a test
that needed a database would be describing something else.
"""

from __future__ import annotations

import re

import pytest

from privaparse.app.catalogue import load_catalogue
from privaparse.app.config import Settings
from privaparse.parser.detection_pass import DetectionPass
from privaparse.parser.detector import Detector
from privaparse.parser.types import SOURCE_COREF, SOURCE_GLINER, EntityType, Span

NAME = "Max Mustermann"


class RecordingDetector:
    """Finds one fixed surface form and remembers every text it was shown.

    ``first_only`` leaves the repeats for the coreference sweep to find, which
    is what tells a run with the sweep apart from one without it.
    """

    def __init__(self, needle: str = NAME, *, score: float = 0.95, first_only: bool = False):
        self.pattern = re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)")
        self.score = score
        self.first_only = first_only
        self.seen: list[str] = []

    def detect(self, text: str) -> list[Span]:
        self.seen.append(text)
        spans = [
            Span(
                start=match.start(),
                end=match.end(),
                text=match.group(0),
                type=EntityType.PERSON,
                score=self.score,
                source=SOURCE_GLINER,
            )
            for match in self.pattern.finditer(text)
        ]
        return spans[:1] if self.first_only else spans


class BatchingDetector(RecordingDetector):
    """A detector that batches, so ``detect_batch``'s probe has something to find."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.batches: list[list[str]] = []

    def detect_many(self, texts) -> list[list[Span]]:
        self.batches.append(list(texts))
        return [self.detect(text) for text in texts]


@pytest.fixture()
def catalogue():
    return load_catalogue()


@pytest.fixture()
def pass_factory(catalogue):
    def build(detector: Detector, **overrides) -> DetectionPass:
        values = {
            "threshold": 0.5,
            "sweep": True,
            "scan_code": False,
            "catalogue": catalogue,
        }
        values.update(overrides)
        return DetectionPass(detector=detector, **values)

    return build


def test_the_detector_only_ever_sees_the_masked_view(pass_factory) -> None:
    """The masked-view invariant, stated in the ``Detector`` protocol's
    docstring and enforced by the pass being its only caller: a name inside a
    code fence is never shown to the detector, so it cannot be found there.
    """
    detector = RecordingDetector()
    text = f'Kontakt: {NAME}.\n\n```python\nuser = "{NAME}"\n```\n'

    spans = pass_factory(detector, sweep=False).run(text)

    assert len(detector.seen) == 1
    assert detector.seen[0].count(NAME) == 1, "the fenced copy must be masked out"
    assert [span.start for span in spans] == [text.index(NAME)]


def test_scan_code_hands_the_detector_the_document_unmasked(pass_factory) -> None:
    detector = RecordingDetector()
    text = f'Kontakt: {NAME}.\n\n```python\nuser = "{NAME}"\n```\n'

    pass_factory(detector, scan_code=True, sweep=False).run(text)

    assert detector.seen == [text]


def test_the_threshold_drops_a_weak_candidate(pass_factory) -> None:
    text = f"{NAME} war da."
    weak = pass_factory(RecordingDetector(score=0.2))

    assert weak.run(text) == []
    assert [span.text for span in weak.replace(threshold=0.1).run(text)] == [NAME]


def test_the_coreference_sweep_finds_the_repeat_only_when_it_is_enabled(
    pass_factory,
) -> None:
    """The detector proposes the first occurrence and nothing else, so the
    second one exists only if the sweep ran.
    """
    text = f"{NAME} kam. Später ging {NAME} wieder."
    swept = pass_factory(RecordingDetector(first_only=True))

    assert [span.source for span in swept.run(text)] == [SOURCE_GLINER, SOURCE_COREF]
    assert [span.source for span in swept.replace(sweep=False).run(text)] == [SOURCE_GLINER]


def test_scan_returns_candidate_spans_and_resolve_rules_on_them(pass_factory) -> None:
    """The expensive half proposes, the cheap half decides — which is what lets
    the threshold sweep re-run the cheap half per point on the curve.
    """
    text = f"{NAME} war da."
    detection_pass = pass_factory(RecordingDetector(score=0.2))

    protected, candidates = detection_pass.scan(text)

    assert [span.text for span in candidates] == [NAME]
    assert protected.original == text
    assert detection_pass.resolve(protected, candidates) == []

    lenient = detection_pass.replace(threshold=0.1)
    assert [span.text for span in lenient.resolve(protected, candidates)] == [NAME]


def test_scan_masks_before_the_detector_and_reports_the_masked_view(pass_factory) -> None:
    text = f"{NAME} war da.\n\n```\n{NAME}\n```\n"

    protected, candidates = pass_factory(RecordingDetector()).scan(text)

    assert protected.view.count(NAME) == 1
    assert [span.start for span in candidates] == [text.index(NAME)]


def test_a_replaced_variant_differs_only_in_what_was_replaced(pass_factory) -> None:
    detector = RecordingDetector()
    original = pass_factory(detector)

    variant = original.replace(threshold=0.9, sweep=False)

    assert (variant.threshold, variant.sweep) == (0.9, False)
    assert variant.scan_code == original.scan_code
    assert variant.catalogue is original.catalogue
    assert variant.detector is detector
    assert (original.threshold, original.sweep) == (0.5, True), "the original is untouched"


def test_single_text_detection_is_batch_detection_over_one_text(pass_factory) -> None:
    text = f"{NAME} kam. Später ging {NAME} wieder."
    detection_pass = pass_factory(RecordingDetector(first_only=True))

    assert detection_pass.run(text) == detection_pass.run_batch([text])[0]


def test_the_batch_entry_point_lets_a_batching_detector_batch(pass_factory) -> None:
    """The pass calls ``detect_batch``, so a detector that defines
    ``detect_many`` sees every text in one call rather than one at a time.
    """
    detector = BatchingDetector()
    texts = [f"{NAME} kam.", "Nichts hier.", f"Auch {NAME}."]

    per_text = pass_factory(detector, sweep=False).run_batch(texts)

    assert detector.batches == [texts]
    assert [len(spans) for spans in per_text] == [1, 0, 1]


def test_from_settings_carries_the_four_values_over(catalogue) -> None:
    settings = Settings(threshold=0.42, coreference_sweep=False, scan_code=True)
    detector = RecordingDetector()

    detection_pass = DetectionPass.from_settings(settings, detector)

    assert detection_pass.threshold == 0.42
    assert detection_pass.sweep is False
    assert detection_pass.scan_code is True
    assert detection_pass.catalogue.types.keys() == catalogue.types.keys()
    assert detection_pass.detector is detector


def test_a_detection_pass_is_not_a_detector(pass_factory) -> None:
    """ADR 0004: the pass must not satisfy the ``Detector`` protocol.

    A detector is handed the masked view and proposes candidate spans; the pass
    is handed the original document and returns what survived. Sharing a method
    name is what let those two contracts be confused, and it would make it legal
    to nest a pass inside a ``CompositeDetector`` — masking a document twice, at
    a seam where the offsets are no longer the ones the caller holds.
    """
    detection_pass = pass_factory(RecordingDetector())

    assert not isinstance(detection_pass, Detector)
