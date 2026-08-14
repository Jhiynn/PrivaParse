"""The gateway's shared-key authentication."""

from __future__ import annotations

KEY = "s3cret-not-a-real-key"
HEADER = "X-PrivaParse-Key"


def test_no_key_configured_means_no_authentication(direct_client):
    # The default every existing user is on, and it must not change.
    assert direct_client.get("/privaparse/catalogue").status_code == 200


def test_a_request_without_the_key_is_rejected(keyed_client):
    response = keyed_client.get("/privaparse/catalogue")
    assert response.status_code == 401
    assert response.json()["error"]["type"] == "invalid_api_key"


def test_a_request_with_the_wrong_key_is_rejected(keyed_client):
    response = keyed_client.get("/privaparse/catalogue", headers={HEADER: "wrong"})
    assert response.status_code == 401


def test_a_request_with_the_right_key_is_allowed(keyed_client):
    response = keyed_client.get("/privaparse/catalogue", headers={HEADER: KEY})
    assert response.status_code == 200


def test_the_proxy_routes_are_covered_too(keyed_client):
    # A test on one route proves nothing about the other; the middleware
    # covers both surfaces and both are worth asserting.
    response = keyed_client.post("/v1/chat/completions", json={"model": "m", "messages": []})
    assert response.status_code == 401


def test_healthz_answers_without_a_key(keyed_client):
    # The container healthcheck curls this. A probe that needs a credential
    # reports the service unhealthy when the credential is misconfigured.
    response = keyed_client.get("/healthz")
    assert response.status_code == 200


def test_the_error_never_echoes_what_was_presented(keyed_client):
    response = keyed_client.get("/privaparse/catalogue", headers={HEADER: "hunter2"})
    assert "hunter2" not in response.text


def test_an_empty_key_setting_authenticates_nothing(empty_key_client):
    # `PRIVAPARSE_API_KEY=` in a .env file produces this. It must mean
    # "no key", not "the empty string is the key".
    assert empty_key_client.get("/privaparse/catalogue").status_code == 200
    assert empty_key_client.get("/privaparse/catalogue", headers={HEADER: ""}).status_code == 200


def test_the_two_credentials_do_not_cross(keyed_client, upstream):
    keyed_client.post(
        "/v1/chat/completions",
        json={"model": "m", "messages": [{"role": "user", "content": "hallo"}]},
        headers={HEADER: KEY, "Authorization": "Bearer sk-the-users-own-key"},
    )
    forwarded = upstream.headers[-1]
    # The client's provider credential still reaches the provider ...
    assert forwarded["authorization"] == "Bearer sk-the-users-own-key"
    # ... and this gateway's own key never does.
    assert HEADER.lower() not in {k.lower() for k in forwarded}
    assert KEY not in str(forwarded)
