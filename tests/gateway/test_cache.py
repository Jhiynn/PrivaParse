"""The detection cache: what may be reused between requests, and what may not.

Detection is the expensive half and depends only on the text and the
catalogue, so it is cached. Resolution and the vault write are not, and the
tests here exist mostly to hold that line: a cached block must still produce
its own mapping, or `reverse` would resolve one request's answer against
another request's mapping.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from privaparse.app.catalogue import load_catalogue
from privaparse.app.config import Settings
from privaparse.database.placeholder import PLACEHOLDER_RE
from privaparse.engine import PrivaParseEngine
from privaparse.gateway.cache import CachingDetector, DetectionCache, catalogue_fingerprint
from privaparse.gateway.server import create_app
from privaparse.parser.types import SOURCE_GLINER, Span


class CountingDetector:
    """Wraps a real detector and records every text it was asked about."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.seen: list[str] = []

    def detect(self, text: str) -> list[Span]:
        self.seen.append(text)
        return self.inner.detect(text)

    def detect_many(self, texts) -> list[list[Span]]:
        self.seen.extend(texts)
        return [self.inner.detect(text) for text in texts]


def _engine(settings: Settings, detector) -> PrivaParseEngine:
    return PrivaParseEngine(settings, detector=detector, configure_logs=False)


def _overlay(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "entities.yaml"
    path.write_text(f"version: 1\nplaceholder_types:\n{body}", encoding="utf-8")
    return path


def _span(text: str) -> Span:
    return Span(start=0, end=len(text), text=text, type="PERSON", score=0.9,
                source=SOURCE_GLINER)


# --- the cache itself ------------------------------------------------------


def test_a_stored_block_comes_back():
    cache = DetectionCache(capacity=4)
    cache.put("fp", "Max Mustermann", [_span("Max Mustermann")])
    assert [s.text for s in cache.get("fp", "Max Mustermann")] == ["Max Mustermann"]


def test_an_unseen_block_is_a_miss():
    cache = DetectionCache(capacity=4)
    assert cache.get("fp", "Max Mustermann") is None


def test_a_different_fingerprint_does_not_see_the_stored_block():
    """The catalogue is half the key. Spans found under one are not evidence
    about another."""
    cache = DetectionCache(capacity=4)
    cache.put("fp-a", "Max Mustermann", [_span("Max Mustermann")])
    assert cache.get("fp-b", "Max Mustermann") is None


def test_the_least_recently_used_block_is_evicted_first():
    cache = DetectionCache(capacity=2)
    cache.put("fp", "eins", [])
    cache.put("fp", "zwei", [])
    cache.get("fp", "eins")  # eins is now the most recent
    cache.put("fp", "drei", [])
    assert cache.get("fp", "zwei") is None
    assert cache.get("fp", "eins") is not None


def test_a_capacity_of_zero_stores_nothing():
    """`PRIVAPARSE_GATEWAY_CACHE=0` has to mean off, not a cache of size zero
    that still counts hits it cannot serve."""
    cache = DetectionCache(capacity=0)
    cache.put("fp", "eins", [])
    assert cache.get("fp", "eins") is None
    assert len(cache) == 0


def test_the_caller_cannot_mutate_what_is_stored():
    """Spans are handed to the merge step, which is free to do what it likes
    with the list it was given."""
    cache = DetectionCache(capacity=4)
    cache.put("fp", "text", [_span("Max Mustermann")])
    first = cache.get("fp", "text")
    first.clear()
    assert len(cache.get("fp", "text")) == 1


def test_hits_and_misses_are_counted():
    """`privaparse gateway stats` reports a hit rate; nothing else reads these."""
    cache = DetectionCache(capacity=4)
    cache.get("fp", "text")
    cache.put("fp", "text", [])
    cache.get("fp", "text")
    assert (cache.hits, cache.misses) == (1, 1)


# --- the fingerprint -------------------------------------------------------


def test_the_same_catalogue_fingerprints_the_same_twice():
    assert catalogue_fingerprint(load_catalogue()) == catalogue_fingerprint(load_catalogue())


def test_changing_a_threshold_changes_the_fingerprint(tmp_path: Path):
    changed = load_catalogue(_overlay(tmp_path, "  EMAIL:\n    threshold: 0.99\n"))
    assert catalogue_fingerprint(changed) != catalogue_fingerprint(load_catalogue())


def test_disabling_a_type_changes_the_fingerprint(tmp_path: Path):
    changed = load_catalogue(_overlay(tmp_path, "  EMAIL:\n    enabled: false\n"))
    assert catalogue_fingerprint(changed) != catalogue_fingerprint(load_catalogue())


def test_changing_a_validator_changes_the_fingerprint(tmp_path: Path):
    changed = load_catalogue(_overlay(tmp_path, "  EMAIL:\n    validator: null\n"))
    assert catalogue_fingerprint(changed) != catalogue_fingerprint(load_catalogue())


def test_changing_a_label_changes_the_fingerprint(tmp_path: Path):
    # `recovery_code` is a documented model label the shipped catalogue routes
    # nowhere, so adding it here claims nothing another type already holds.
    overlay = _overlay(tmp_path, "  SECRET:\n    labels: [password, recovery_code]\n")
    changed = load_catalogue(overlay)
    assert catalogue_fingerprint(changed) != catalogue_fingerprint(load_catalogue())


# --- the caching detector --------------------------------------------------


def test_a_repeated_block_is_not_detected_twice(settings, fake_detector):
    counting = CountingDetector(fake_detector)
    engine = _engine(settings, counting)
    detector = CachingDetector(engine, DetectionCache(capacity=8))

    detector.detect_many(["Hallo Max Mustermann"])
    detector.detect_many(["Hallo Max Mustermann"])

    assert counting.seen == ["Hallo Max Mustermann"]


def test_a_cached_block_returns_the_same_spans(settings, fake_detector):
    engine = _engine(settings, CountingDetector(fake_detector))
    detector = CachingDetector(engine, DetectionCache(capacity=8))

    first = detector.detect_many(["Hallo Max Mustermann"])[0]
    second = detector.detect_many(["Hallo Max Mustermann"])[0]

    assert [(s.start, s.end, s.text, s.type) for s in first] == [
        (s.start, s.end, s.text, s.type) for s in second
    ]
    assert first


def test_only_the_new_block_of_a_batch_reaches_the_detector(settings, fake_detector):
    """The case the cache exists for: a coding agent resends its whole history
    with one turn appended."""
    counting = CountingDetector(fake_detector)
    engine = _engine(settings, counting)
    detector = CachingDetector(engine, DetectionCache(capacity=8))

    detector.detect_many(["erste Nachricht", "zweite Nachricht"])
    detector.detect_many(["erste Nachricht", "zweite Nachricht", "dritte Nachricht"])

    assert counting.seen == ["erste Nachricht", "zweite Nachricht", "dritte Nachricht"]


def test_a_batch_repeating_a_block_detects_it_once(settings, fake_detector):
    counting = CountingDetector(fake_detector)
    engine = _engine(settings, counting)
    detector = CachingDetector(engine, DetectionCache(capacity=8))

    detector.detect_many(["gleich", "gleich"])

    assert counting.seen == ["gleich"]


def test_a_partial_hit_keeps_every_block_in_its_own_place(settings, fake_detector):
    """Spans must line up with the text they came from. A cache that returns
    the right spans against the wrong index corrupts the document."""
    engine = _engine(settings, CountingDetector(fake_detector))
    detector = CachingDetector(engine, DetectionCache(capacity=8))

    detector.detect_many(["nichts hier"])
    result = detector.detect_many(
        ["Hallo Max Mustermann", "nichts hier", "Gruss Erika Musterfrau"]
    )

    assert [[s.text for s in spans] for spans in result] == [
        ["Max Mustermann"],
        [],
        ["Erika Musterfrau"],
    ]


def test_a_changed_catalogue_is_detected_again(settings, fake_detector, tmp_path: Path):
    """An operator edits the catalogue and the gateway reloads. Spans found
    under the old one describe a catalogue that no longer exists."""
    cache = DetectionCache(capacity=8)
    first = CountingDetector(fake_detector)
    CachingDetector(_engine(settings, first), cache).detect_many(["Hallo Max Mustermann"])

    reloaded = Settings(
        db_path=settings.db_path,
        device="cpu",
        detector="regex",
        catalogue_path=_overlay(tmp_path, "  EMAIL:\n    threshold: 0.99\n"),
    )
    second = CountingDetector(fake_detector)
    CachingDetector(_engine(reloaded, second), cache).detect_many(["Hallo Max Mustermann"])

    assert second.seen == ["Hallo Max Mustermann"]


def test_a_cached_block_never_reaches_the_detector(settings, fake_detector):
    """Not merely "the same spans come back": the detector is not consulted at
    all. The real one is built on first use, so touching it on a hit would
    pull 1.2 GB of weights to answer a question already answered."""

    class Exploding:
        def detect(self, text):  # pragma: no cover - reaching this fails the test
            raise AssertionError("detection ran on a cached block")

        def detect_many(self, texts):  # pragma: no cover - same
            raise AssertionError("detection ran on a cached block")

    cache = DetectionCache(capacity=8)
    CachingDetector(_engine(settings, fake_detector), cache).detect_many(["Hallo Max Mustermann"])

    cold = CachingDetector(_engine(settings, Exploding()), cache)
    assert [s.text for s in cold.detect_many(["Hallo Max Mustermann"])[0]] == ["Max Mustermann"]


# --- what the cache must not skip ------------------------------------------


def test_a_cached_block_still_gets_its_own_mapping(settings, fake_detector):
    """Only detection is cached. A second request that reuses the spans must
    still write its own mapping, or `reverse` would resolve its answer against
    a mapping it never issued."""
    engine = _engine(settings, CountingDetector(fake_detector))
    detector = CachingDetector(engine, DetectionCache(capacity=8))

    first = engine.pseudonymize_batch(["Hallo Max Mustermann"], detector=detector)
    second = engine.pseudonymize_batch(["Hallo Max Mustermann"], detector=detector)

    assert first.mapping_id != second.mapping_id
    placeholder = PLACEHOLDER_RE.search(second.texts[0]).group(0)
    assert engine.reverse(second.mapping_id, placeholder).text == "Max Mustermann"


def test_the_second_request_is_pseudonymised_and_restored_like_the_first(
    settings, fake_detector, upstream
):
    counting = CountingDetector(fake_detector)
    engine = _engine(settings, counting)
    client = TestClient(create_app(settings, engine=engine, upstream=upstream))
    upstream.echo_with(
        lambda sent: sent["messages"][-1]["content"],
        lambda text: {"choices": [{"index": 0, "finish_reason": "stop",
                                   "message": {"role": "assistant", "content": text}}]},
    )
    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hallo Max Mustermann"}]}

    first = client.post("/v1/chat/completions", json=body)
    second = client.post("/v1/chat/completions", json=body)

    sent = upstream.requests[-1]["messages"][0]["content"]
    assert "Max Mustermann" not in sent
    answer = second.json()["choices"][0]["message"]["content"]
    assert answer == first.json()["choices"][0]["message"]["content"] == "Hallo Max Mustermann"
    # One detection for two requests -- the whole point.
    assert counting.seen == ["Hallo Max Mustermann"]


def test_the_cache_can_be_turned_off(settings, fake_detector, upstream):
    off = settings.model_copy(update={"gateway_cache": 0})
    counting = CountingDetector(fake_detector)
    engine = _engine(off, counting)
    client = TestClient(create_app(off, engine=engine, upstream=upstream))
    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hallo Max Mustermann"}]}

    client.post("/v1/chat/completions", json=body)
    client.post("/v1/chat/completions", json=body)

    assert counting.seen == ["Hallo Max Mustermann"] * 2


def test_the_cap_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRIVAPARSE_GATEWAY_CACHE", "7")
    assert Settings().gateway_cache == 7
