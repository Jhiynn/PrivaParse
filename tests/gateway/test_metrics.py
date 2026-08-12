"""What the gateway counts, and what it refuses to count.

Everything here is a number. An operator needs to know the gateway is working
and roughly what it costs; none of that requires keeping a single character of
anyone's text, and the tests below are what keeps it that way.
"""

from __future__ import annotations

import json

from starlette.testclient import TestClient

from privaparse.engine import PrivaParseEngine
from privaparse.gateway.metrics import Metrics
from privaparse.gateway.server import STATS_PATH, create_app


def test_a_fresh_metrics_object_reports_zeroes():
    metrics = Metrics()
    assert (metrics.requests, metrics.entities_per_request, metrics.p50_seconds) == (0, 0.0, 0.0)


def test_entities_per_request_is_an_average():
    metrics = Metrics()
    metrics.record(entities=1, seconds=0.1)
    metrics.record(entities=4, seconds=0.1)
    assert metrics.entities_per_request == 2.5


def test_the_median_of_an_odd_number_of_samples_is_the_middle_one():
    metrics = Metrics()
    for seconds in (0.3, 0.1, 0.2):
        metrics.record(entities=0, seconds=seconds)
    assert metrics.p50_seconds == 0.2


def test_the_median_of_an_even_number_of_samples_averages_the_middle_pair():
    metrics = Metrics()
    for seconds in (0.1, 0.2, 0.3, 0.4):
        metrics.record(entities=0, seconds=seconds)
    assert metrics.p50_seconds == 0.25


def test_only_the_most_recent_samples_are_kept():
    """A long-running gateway must not grow a latency list forever, and a
    median over a week of samples describes nothing an operator can act on."""
    metrics = Metrics(window=3)
    for seconds in (9.0, 9.0, 0.1, 0.2, 0.3):
        metrics.record(entities=0, seconds=seconds)
    assert metrics.p50_seconds == 0.2


def test_the_request_count_survives_a_request_that_found_nothing():
    metrics = Metrics()
    metrics.record(entities=0, seconds=0.1)
    assert metrics.requests == 1


# --- the route -------------------------------------------------------------


def _client(settings, detector, upstream) -> TestClient:
    engine = PrivaParseEngine(settings, detector=detector, configure_logs=False)
    return TestClient(create_app(settings, engine=engine, upstream=upstream))


def test_the_stats_route_reports_what_went_through(settings, fake_detector, upstream):
    client = _client(settings, fake_detector, upstream)
    client.post("/v1/chat/completions", json={
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hallo Max Mustermann"}],
    })

    body = client.get(STATS_PATH).json()

    assert body["requests"] == 1
    assert body["entities_per_request"] == 1.0
    assert body["cache"]["misses"] == 1


def test_the_second_identical_request_shows_up_as_a_cache_hit(
    settings, fake_detector, upstream
):
    client = _client(settings, fake_detector, upstream)
    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hallo Max Mustermann"}]}
    client.post("/v1/chat/completions", json=body)
    client.post("/v1/chat/completions", json=body)

    cache = client.get(STATS_PATH).json()["cache"]

    assert (cache["hits"], cache["misses"]) == (1, 1)
    assert cache["hit_rate"] == 0.5


def test_a_refused_request_is_not_counted_as_one_that_worked(
    settings, fake_detector, upstream
):
    """A 502 never reached the provider and pseudonymised nothing. Counting it
    would make the entities-per-request average describe requests that had no
    entities because they never ran."""
    client = _client(settings, fake_detector, upstream)
    client.post("/v1/chat/completions", json={
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hallo"}],
        "some_new_field": "Max Mustermann",
    })

    assert client.get(STATS_PATH).json()["requests"] == 0


def test_the_stats_route_holds_no_text_at_all(settings, fake_detector, upstream):
    """The one property that matters here. Everything reported is a number."""
    client = _client(settings, fake_detector, upstream)
    client.post("/v1/chat/completions", json={
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hallo Max Mustermann"}],
    })

    raw = client.get(STATS_PATH).text

    assert "Max Mustermann" not in raw
    assert "PERSON" not in raw
    for value in _leaves(json.loads(raw)):
        assert isinstance(value, (int, float)), value


def _leaves(value):
    if isinstance(value, dict):
        for sub in value.values():
            yield from _leaves(sub)
    else:
        yield value
