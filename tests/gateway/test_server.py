from __future__ import annotations

from starlette.testclient import TestClient

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


def test_the_gateway_stores_no_credential(settings, fake_detector, upstream):
    from privaparse.engine import PrivaParseEngine

    engine = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    app = create_app(settings, engine=engine, upstream=upstream)
    client = TestClient(app)
    client.get("/v1/models", headers={"authorization": "Bearer sk-secret"})
    assert "sk-secret" not in repr(app.state.__dict__)
