"""The gateway's shared-key authentication."""

from __future__ import annotations

import asyncio

import httpx

from privaparse.engine import PrivaParseEngine
from privaparse.gateway.server import create_app

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


def test_a_malformed_key_still_gets_401_not_500(settings, upstream):
    """A raw byte above 0x7f in the header used to raise `TypeError` inside
    `hmac.compare_digest` -- Starlette decodes header bytes through latin-1,
    which never fails, so the middleware received a `str` compare_digest
    then refused outright. `ServerErrorMiddleware` turned that into a bare
    500 with no `_error()` envelope, on the one route standing in front of
    `/privaparse/reverse`.

    `TestClient` cannot exercise this: it validates headers client-side and
    refuses to send the offending bytes before they ever reach the app. Only
    a transport that skips that validation will actually deliver them, so
    this drives the ASGI app directly through `httpx.ASGITransport` -- the
    header value is passed as raw `bytes`, which httpx forwards unchecked,
    rather than as `str`, which httpx would refuse to encode itself.
    """
    keyed_settings = settings.model_copy(update={"api_key": KEY})
    engine = PrivaParseEngine(keyed_settings, configure_logs=False)
    app = create_app(keyed_settings, engine=engine, upstream=upstream)

    async def send_malformed_key() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(
                "/privaparse/catalogue",
                headers=[(HEADER.encode("ascii"), b"\xff\xfeabc")],
            )

    response = asyncio.run(send_malformed_key())
    assert response.status_code == 401
    assert response.json()["error"]["type"] == "invalid_api_key"


def test_a_legitimately_non_ascii_key_still_authenticates(settings, upstream):
    """The malformed-key fix must not overcorrect into rejecting a real key
    that merely is not ASCII -- an umlaut is ordinary, valid UTF-8, accepted
    by `Settings.api_key`'s validator, and should authenticate exactly like
    any other key.

    Sent the same way as the malformed-key test and for the same reason:
    `TestClient`/`httpx`'s own header encoding refuses a plain `str` header
    value that is not ASCII, so this presents the key as its UTF-8 bytes
    directly, which is what a real client speaking raw HTTP would put on the
    wire for a non-ASCII header value.
    """
    umlaut_key = "schlüssel-42"
    keyed_settings = settings.model_copy(update={"api_key": umlaut_key})
    engine = PrivaParseEngine(keyed_settings, configure_logs=False)
    app = create_app(keyed_settings, engine=engine, upstream=upstream)

    async def send_umlaut_key() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(
                "/privaparse/catalogue",
                headers=[(HEADER.encode("ascii"), umlaut_key.encode("utf-8"))],
            )

    response = asyncio.run(send_umlaut_key())
    assert response.status_code == 200


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
