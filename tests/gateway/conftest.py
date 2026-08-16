from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from starlette.testclient import TestClient

from privaparse.engine import PrivaParseEngine
from privaparse.gateway.server import create_app
from privaparse.gateway.upstream import Upstream

#: Given a forwarded request, the text the gateway sent.
EchoReader = Callable[[dict], str]
#: Given that text, an answer carrying it in one protocol's own shape.
EchoWriter = Callable[[str], dict]


class FakeUpstream:
    """Stands in for the provider. Records every request it was handed.

    Headers are recorded through `Upstream._headers`, the same allow-list the
    real client filters through, so `.headers` reflects what would actually
    leave the machine -- not the full incoming request. Without this, a test
    asserting a header never reaches the provider (e.g. the gateway's own
    auth key) would see it here anyway and fail for the wrong reason.
    """

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        #: Set by a test that wants to see how the gateway handles a provider
        #: error -- a rate limit, an auth failure -- rather than a completion.
        self.status: int = 200
        #: A reader and a writer, set through `echo_with`: the reply carries
        #: back whatever text arrived. A real model repeats a placeholder back
        #: at you constantly -- it looks like a name to it -- and that is the
        #: only way a test can see a placeholder it could not have known in
        #: advance.
        #:
        #: Both halves come from the caller rather than from this class,
        #: because where the text sits in a request and how an answer carries
        #: it are the protocol's business. A fake that knew one protocol's
        #: shape could only echo for that one, which is how an assertion comes
        #: to exist for one adapter and not for its peer.
        self.echo: tuple[EchoReader, EchoWriter] | None = None
        #: Optional callable(body) -> reply, for a test whose expected answer
        #: has to contain a placeholder only this request could have issued.
        self.reply_for = None
        #: The streaming equivalent: callable(body) -> list[bytes]. A streamed
        #: answer that has to echo a placeholder cannot be written in advance
        #: either -- only the request knows which one was issued.
        self.chunks_for = None
        self.reply: dict = {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "ok"},
                 "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        self.chunks: list[bytes] = []

    def echo_with(self, read: EchoReader, write: EchoWriter) -> None:
        """Answer every request with the text it carried, in `write`'s shape."""
        self.echo = (read, write)

    async def post_json(self, path, body, headers):
        self.requests.append(json.loads(json.dumps(body)))
        self.headers.append(Upstream._headers(headers))
        reply = self.reply
        if self.reply_for is not None:
            reply = self.reply_for(body)
        elif self.echo is not None:
            read, write = self.echo
            reply = write(read(body))
        return self.status, reply, {"content-type": "application/json"}

    async def get_json(self, path, headers):
        # No request body on a GET, so nothing is appended to `requests` --
        # `.last` stays meaningful for the POST endpoints later tasks add.
        self.headers.append(Upstream._headers(headers))
        return self.status, self.reply, {"content-type": "application/json"}

    async def stream(self, path, body, headers):
        self.requests.append(json.loads(json.dumps(body)))
        self.headers.append(Upstream._headers(headers))
        chunks = self.chunks if self.chunks_for is None else self.chunks_for(body)
        for chunk in chunks:
            yield chunk

    @property
    def last(self) -> dict:
        return self.requests[-1]


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream()


@pytest.fixture()
def direct_client(settings, upstream) -> TestClient:
    """A client against the direct API, backed by a regex-only engine.

    No model is loaded -- `settings` (from the root conftest) pins
    `detector="regex"`, which is all the direct-API tests need. Follows the
    same construction the other gateway tests use inline: a real engine over
    an injected upstream, wrapped in Starlette's `TestClient`.
    """
    engine = PrivaParseEngine(settings, configure_logs=False)
    return TestClient(create_app(settings, engine=engine, upstream=upstream))


#: Must match the `KEY` constant in tests/gateway/test_auth.py -- the "right
#: key" tests on that side present exactly this value.
KEY = "s3cret-not-a-real-key"


@pytest.fixture()
def keyed_client(settings, upstream) -> TestClient:
    """`direct_client`, but with a shared key configured.

    Every route but /healthz now demands `X-PrivaParse-Key: KEY`.
    """
    keyed_settings = settings.model_copy(update={"api_key": KEY})
    engine = PrivaParseEngine(keyed_settings, configure_logs=False)
    return TestClient(create_app(keyed_settings, engine=engine, upstream=upstream))


@pytest.fixture()
def empty_key_client(settings, upstream) -> TestClient:
    """`direct_client`, with `api_key` set to the empty string explicitly.

    Distinct from the plain `settings` default in intent, not in value:
    `PRIVAPARSE_API_KEY=` in a `.env` file produces this, and it must mean
    "no key configured", not "the empty string is the key".
    """
    empty_settings = settings.model_copy(update={"api_key": ""})
    engine = PrivaParseEngine(empty_settings, configure_logs=False)
    return TestClient(create_app(empty_settings, engine=engine, upstream=upstream))
