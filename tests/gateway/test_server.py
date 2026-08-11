from __future__ import annotations

import io

from starlette.testclient import TestClient

from privaparse.app.logging import configure_logging
from privaparse.gateway.server import create_app


def test_healthz_reports_ready(settings, fake_detector, upstream):
    from privaparse.engine import PrivaParseEngine

    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    client = TestClient(create_app(settings, engine=engine, upstream=upstream))
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_models_is_proxied_upstream_untouched(settings, fake_detector, upstream):
    from privaparse.engine import PrivaParseEngine

    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    upstream.reply = {"object": "list", "data": [{"id": "gpt-4o"}]}
    client = TestClient(create_app(settings, engine=engine, upstream=upstream))
    response = client.get("/v1/models", headers={"authorization": "Bearer sk-test"})
    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "gpt-4o"
    # The key belongs to the caller and must reach the provider unchanged.
    assert upstream.headers[-1]["authorization"] == "Bearer sk-test"


def test_chat_completions_refuses_and_forwards_nothing(settings, fake_detector, upstream):
    """This task's one named purpose. Nothing is pseudonymised yet, so nothing
    may leave the machine through this route. A 501 returned *after*
    forwarding would satisfy the status-code assertion and leak regardless --
    the second assertion is the one that actually proves fail-closed, the
    same shape as the assertion the design names as load-bearing for Task 3.
    """
    from privaparse.engine import PrivaParseEngine

    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    client = TestClient(create_app(settings, engine=engine, upstream=upstream))
    response = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hallo Max"}]},
        headers={"authorization": "Bearer sk-test"},
    )
    assert response.status_code == 501
    assert upstream.requests == []


def test_the_gateway_does_not_stash_the_credential_on_app_state(settings, fake_detector, upstream):
    """Narrow on purpose: this checks one specific place a credential could be
    kept around after the request -- app.state. It does not check logging or
    stdout; see test_the_gateway_does_not_log_the_credential for the former.
    """
    from privaparse.engine import PrivaParseEngine

    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    app = create_app(settings, engine=engine, upstream=upstream)
    client = TestClient(app)
    client.get("/v1/models", headers={"authorization": "Bearer sk-secret"})
    assert "sk-secret" not in repr(app.state.__dict__)


def test_the_gateway_does_not_log_the_credential(settings, fake_detector, upstream):
    """The other half of 'no credential is stored, ever': the log stream.

    Uses the same capture pattern as test_logging.py -- an explicit
    StreamHandler wired through configure_logging() -- rather than pytest's
    caplog. caplog attaches at the root logger and only sees records that
    propagate there; configure_logging() sets propagate=False on the
    `privaparse` logger, and once anything in the session calls it (e.g.
    test_logging.py), that stays false for every test that runs after, in
    any order. A caplog-based version of this test would silently stop
    seeing records and pass for the wrong reason -- exactly the kind of test
    this plan is trying to not ship.
    """
    from privaparse.engine import PrivaParseEngine

    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    client = TestClient(create_app(settings, engine=engine, upstream=upstream))

    stream = io.StringIO()
    configure_logging("DEBUG", stream=stream)
    client.get("/v1/models", headers={"authorization": "Bearer sk-secret"})

    assert "sk-secret" not in stream.getvalue()
