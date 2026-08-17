"""Engine wiring: the model cache, the shared-engine lifecycle, the detection surface.

The detection tests here are about the *engine's* half of detection — that both
its entry points come off one pass, that an injected detector reaches it, and
that injecting one never wakes the model. What the pass then does with a
document belongs to `test_detection_pass.py`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from privaparse.app.config import Settings
from privaparse.engine import PrivaParseEngine, _point_hf_cache_at
from privaparse.parser.types import SOURCE_COREF, SOURCE_GLINER, EntityType, Span


@pytest.fixture(autouse=True)
def clean_hf_home(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("HF_HOME", raising=False)
    yield


def test_model_dir_becomes_the_hugging_face_cache(tmp_path: Path) -> None:
    """It was a setting that read nicely and did nothing — weights landed in
    ~/.cache/huggingface regardless, costing a second 1.2 GB download."""
    target = tmp_path / "weights"
    _point_hf_cache_at(target)

    assert os.environ["HF_HOME"] == str(target.resolve())
    assert target.exists()


def test_an_explicit_hf_home_is_respected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Someone who set HF_HOME meant it."""
    monkeypatch.setenv("HF_HOME", "/somewhere/deliberate")
    _point_hf_cache_at(tmp_path / "weights")

    assert os.environ["HF_HOME"] == "/somewhere/deliberate"


def test_engine_sets_the_cache_before_the_model_could_load(tmp_path: Path) -> None:
    settings = Settings(
        db_path=tmp_path / "vault.db",
        model_dir=tmp_path / "weights",
        device="cpu",
        detector="regex",
    )
    engine = PrivaParseEngine(settings, configure_logs=False)
    try:
        assert os.environ["HF_HOME"] == str((tmp_path / "weights").resolve())
    finally:
        engine.close()


def test_offline_mode_cuts_out_the_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tool that promises the document never leaves the machine should be
    able to prove it does not phone home either."""
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)

    settings = Settings(
        db_path=tmp_path / "vault.db", device="cpu", detector="regex", offline=True
    )
    engine = PrivaParseEngine(settings, configure_logs=False)
    try:
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
    finally:
        engine.close()


def test_offline_is_off_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The first run has to be able to fetch the weights."""
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)

    settings = Settings(db_path=tmp_path / "vault.db", device="cpu", detector="regex")
    engine = PrivaParseEngine(settings, configure_logs=False)
    try:
        assert "HF_HUB_OFFLINE" not in os.environ
    finally:
        engine.close()


def test_engine_is_reusable_across_calls(tmp_path: Path) -> None:
    """A service builds one engine at startup; the vault must survive calls."""
    settings = Settings(db_path=tmp_path / "vault.db", device="cpu", detector="regex")
    engine = PrivaParseEngine(settings, configure_logs=False)
    try:
        first = engine.pseudonymize("Mail an max@test.de", source_name="a.md")
        second = engine.pseudonymize("Wieder max@test.de", source_name="b.md")

        assert first.spans[0].placeholder == second.spans[0].placeholder
        assert engine.vault_stats().mappings == 2
    finally:
        engine.close()


# --- detection --------------------------------------------------------------

NAME = "Max Mustermann"

#: A name the regex-only detector `settings` configures finds nothing in,
#: which is what makes an injected detector's work visible.
NAMED = f"{NAME} schrieb. Später rief {NAME} noch einmal an."

#: The same name three times: once in prose, once inside a code fence, once
#: more in prose. Every decision the pass makes is visible in the answer —
#: masking (the fenced copy is never offered), and the coreference sweep (the
#: detector below proposes only the first hit, so the third occurrence exists
#: in the result only if the sweep ran).
FENCED = f'{NAME} schrieb.\n\n```python\nuser = "{NAME}"\n```\n\nSpäter rief {NAME} an.\n'


class FirstHitDetector:
    """Proposes the first occurrence of one name and nothing else.

    Leaves the repeat for the coreference sweep to find, which is what tells a
    run that went through the whole pass apart from one that only called a
    detector. `conftest`'s `fake_detector` cannot: it finds every occurrence
    itself, so its answer is the same with the sweep on or off.
    """

    def __init__(self, needle: str = NAME) -> None:
        self._pattern = re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)")

    def detect(self, text: str) -> list[Span]:
        match = self._pattern.search(text)
        if match is None:
            return []
        return [
            Span(
                start=match.start(),
                end=match.end(),
                text=match.group(0),
                type=EntityType.PERSON,
                score=0.95,
                source=SOURCE_GLINER,
            )
        ]


def test_detect_and_detect_many_answer_one_document_the_same_way(settings) -> None:
    """The drift issue #9 was filed about, pinned rather than assumed.

    True by construction now — both are one line onto the same pass, and the
    pass's own single-text form is its batch form over a one-element sequence
    — but "by construction" is a property of today's implementation, and the
    two used to be written longhand at separate call sites.

    Equality alone would hold between two entry points that both did nothing,
    or that both skipped the sweep. So the document is one where every step of
    the pass leaves a mark, and what the spans are is asserted before the two
    forms are compared: `detect` rewritten longhand without the mask or
    without the sweep fails here, rather than agreeing with a `detect_many`
    that lost the same step.
    """
    engine = PrivaParseEngine(settings, detector=FirstHitDetector(), configure_logs=False)
    try:
        spans = engine.detect(FENCED)

        # The fenced copy is masked out; the third occurrence is the sweep's.
        assert [span.start for span in spans] == [FENCED.index(NAME), FENCED.rindex(NAME)]
        assert [span.source for span in spans] == [SOURCE_GLINER, SOURCE_COREF]

        assert spans == engine.detect_many([FENCED])[0]
    finally:
        engine.close()


def test_detect_accepts_an_injected_detector_and_does_not_load_the_model(
    settings, fake_detector
) -> None:
    """The gateway's single-text path, which used to have no way to say this.

    ``detect`` took no detector, so the gateway could only inject its caching
    wrapper into the batch form: one text node and several came off different
    assemblies. Passing one in must also leave the engine's own detector
    unbuilt — the build is lazy, and a caller that always injects one must
    never trigger the model load.
    """
    engine = PrivaParseEngine(settings, configure_logs=False)
    try:
        spans = engine.detect(NAMED, detector=fake_detector)

        # The regex-only detector `settings` configures finds no names at all,
        # so a PERSON span can only have come from the injected detector.
        assert [span.type for span in spans] == [EntityType.PERSON, EntityType.PERSON]
        assert engine._detector is None  # proving laziness, not using the public API
    finally:
        engine.close()


def test_the_detection_pass_carries_the_engines_settings_and_an_injected_detector(
    settings, fake_detector
) -> None:
    """The accessor both detection methods delegate to: the engine's four
    settings values, and whichever detector the call names. Reading it must
    not wake the model either — it is the same lazy read the methods do.
    """
    engine = PrivaParseEngine(settings, configure_logs=False)
    try:
        detection_pass = engine.detection_pass(detector=fake_detector)

        assert detection_pass.detector is fake_detector
        assert detection_pass.threshold == settings.threshold
        assert detection_pass.sweep == settings.coreference_sweep
        assert detection_pass.scan_code == settings.scan_code
        assert detection_pass.catalogue is settings.catalogue
        assert engine._detector is None
    finally:
        engine.close()


def test_the_detection_pass_defaults_to_the_engines_own_detector(engine, fake_detector) -> None:
    assert engine.detection_pass().detector is fake_detector


def test_detect_many_applies_the_threshold_unlike_the_raw_detector(settings):
    """The raw detector output includes everything, however weak a score --
    detect_many must not: it is the whole detection pass, not a pass-through.
    Mirrors test_detect_raw_returns_unfiltered_spans below, but for the entry
    point that decides rather than the one that only proposes.
    """
    from privaparse.parser.detector import StaticDetector, detect_batch

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

    static = StaticDetector([weak])
    engine = PrivaParseEngine(settings, detector=static, configure_logs=False)
    try:
        raw = detect_batch(static, [text])
        resolved = engine.detect_many([text])

        assert weak in raw[0]
        assert weak not in resolved[0]
    finally:
        engine.close()


def test_detect_many_accepts_an_injected_detector(settings, fake_detector):
    """Mirrors pseudonymize_batch's own injectable-detector signature -- the
    gateway relies on this to put a caching wrapper in front of detection
    without the engine building or owning that detector itself. Passing one
    in must not force the engine to build its own default detector: the
    build is read lazily, so a caller that always injects one never triggers
    it.
    """
    engine = PrivaParseEngine(settings, configure_logs=False)
    try:
        result = engine.detect_many(["Bitte an max@test.de senden."], detector=fake_detector)
        assert result[0]
        assert result[0][0].type == "EMAIL"
        assert engine._detector is None  # proving laziness, not using the public API
    finally:
        engine.close()


def test_detect_raw_returns_unfiltered_spans(settings):
    """``isinstance(spans, list)`` would pass even if detect_raw quietly ran
    the merge/threshold step and returned an empty or fully-filtered list —
    it does not test the one thing detect_raw promises over detect(): that
    nothing has been dropped yet. A span scored below the merge threshold is
    the direct way to show that: detect_raw must still return it, and
    detect() — which does run resolve_spans — must not.
    """
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
