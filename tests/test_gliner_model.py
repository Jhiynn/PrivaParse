"""Tests that load the real GLiNER2 weights.

Skipped by default (``addopts = -m 'not model'``). Run with::

    pytest -m model

These are not accuracy tests — that is what the eval harness is for. They check
the contract between PrivaParse and the library: that offsets point where they
claim to, that batching preserves them, and that the configured device is the
one actually used.
"""

from __future__ import annotations

import pytest

from privaparse.app.config import Settings
from privaparse.app.device import resolve_device
from privaparse.parser.types import EntityType

pytestmark = pytest.mark.model

SAMPLE = (
    "Sehr geehrter Herr Max Mustermann,\n\n"
    "vielen Dank für Ihre Nachricht. Frau Erika Musterfrau meldet sich bei Ihnen.\n\n"
    "Mit freundlichen Grüßen\nSabine Becker"
)


@pytest.fixture(scope="module")
def model_settings() -> Settings:
    # compile=False so the module-scoped fixture does not spend a minute in
    # torch.compile for a handful of assertions.
    return Settings(device="auto", detector="gliner", compile=False, warmup=True)


@pytest.fixture(scope="module")
def gliner(model_settings: Settings):
    from privaparse.parser.gliner_detector import GlinerDetector

    return GlinerDetector(model_settings, resolve_device(model_settings))


def test_model_loads_and_finds_german_names(gliner) -> None:
    spans = gliner.detect(SAMPLE)
    persons = [s.text for s in spans if s.type is EntityType.PERSON]
    assert persons, f"no person found in the sample; got {spans}"


def test_every_returned_span_slices_back_to_its_own_text(gliner) -> None:
    """The invariant the whole rewriting step depends on."""
    for span in gliner.detect(SAMPLE):
        assert span.verify_against(SAMPLE), f"{span} does not match the source text"


def test_offsets_survive_chunking_of_a_long_document(model_settings: Settings) -> None:
    from privaparse.parser.gliner_detector import GlinerDetector, chunk_text

    filler = "Dies ist ein Absatz ohne personenbezogene Daten.\n\n" * 60
    text = filler + "Zum Schluss meldet sich Max Mustermann.\n"

    settings = model_settings.model_copy(update={"chunk_chars": 800})
    assert len(chunk_text(text, settings.chunk_chars)) > 1

    detector = GlinerDetector(settings, resolve_device(settings))
    spans = detector.detect(text)

    for span in spans:
        assert span.verify_against(text)
    assert any("Mustermann" in s.text for s in spans), "entity at the end of a long document was lost"


def test_batching_does_not_change_the_result(model_settings: Settings) -> None:
    from privaparse.parser.gliner_detector import GlinerDetector

    text = ("Absatz über Max Mustermann.\n\n" * 12) + "Ende mit Erika Musterfrau."

    small = model_settings.model_copy(update={"chunk_chars": 400, "batch_size": 1})
    large = model_settings.model_copy(update={"chunk_chars": 400, "batch_size": 16})

    a = GlinerDetector(small, resolve_device(small)).detect(text)
    b = GlinerDetector(large, resolve_device(large)).detect(text)

    assert {(s.start, s.end, str(s.type)) for s in a} == {
        (s.start, s.end, str(s.type)) for s in b
    }


def test_the_model_lands_on_the_configured_device(model_settings: Settings) -> None:
    import torch

    if not torch.cuda.is_available():
        pytest.skip("no CUDA on this machine")

    from privaparse.parser.gliner_detector import GlinerDetector

    settings = model_settings.model_copy(update={"device": "cuda"})
    detector = GlinerDetector(settings, resolve_device(settings))

    parameters = [p for p in _iter_parameters(detector._model)]
    assert parameters, "could not reach the model parameters"
    assert all(p.device.type == "cuda" for p in parameters[:20])


def test_swapping_cpu_for_gpu_does_not_change_what_is_detected(
    model_settings: Settings,
) -> None:
    """Being swappable is worth nothing if the swap changes the answers.

    The GPU path runs fp16 while the CPU path runs fp32, so this is a real
    question, not a formality: silently detecting fewer names after a device
    change would be a disclosure introduced by a config flag.
    """
    import torch

    if not torch.cuda.is_available():
        pytest.skip("no CUDA on this machine")

    from privaparse.parser.gliner_detector import GlinerDetector

    text = (
        "Sehr geehrte Frau Dr. Katharina Weiß,\n\n"
        "Herr Max von Bergen und Frau Müller-Lüdenscheidt haben zugesagt. "
        "Rückfragen an s.becker@musterfirma.de oder +49 170 1234567.\n\n"
        "Mit freundlichen Grüßen\nAhmet Öztürk"
    )

    on_cpu = model_settings.model_copy(update={"device": "cpu"})
    on_gpu = model_settings.model_copy(update={"device": "cuda", "quantize": True})

    cpu_spans = GlinerDetector(on_cpu, resolve_device(on_cpu)).detect(text)
    gpu_spans = GlinerDetector(on_gpu, resolve_device(on_gpu)).detect(text)

    def key(spans):
        return sorted((s.start, s.end, str(s.type)) for s in spans)

    assert key(cpu_spans) == key(gpu_spans), (
        "device swap changed the detections — fp16 on GPU is not equivalent to "
        "fp32 on CPU for this model"
    )


def test_full_pipeline_round_trip_with_the_real_model(tmp_path) -> None:
    from privaparse.engine import PrivaParseEngine

    settings = Settings(
        db_path=tmp_path / "vault.db", device="auto", detector="hybrid", compile=False
    )
    engine = PrivaParseEngine(settings, configure_logs=False)
    try:
        text = "Max Mustermann, max@test.de, +49 170 1234567"
        result = engine.pseudonymize(text)
        restored = engine.reverse(result.mapping_id, result.text)

        assert restored.text == text
        assert "[[EMAIL_" in result.text
        assert "[[PHONE_" in result.text
    finally:
        engine.close()


def _iter_parameters(model):
    """GLiNER2 wraps the encoder; find the parameters wherever they live."""
    import torch

    if isinstance(model, torch.nn.Module):
        yield from model.parameters()
        return
    for name in dir(model):
        if name.startswith("_"):
            continue
        try:
            attribute = getattr(model, name)
        except Exception:
            continue
        if isinstance(attribute, torch.nn.Module):
            yield from attribute.parameters()
            return
