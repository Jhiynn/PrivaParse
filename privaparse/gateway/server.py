"""Starlette app: the process the rest of the gateway attaches to.

`/healthz` proves the process is up, `/v1/models` proxies untouched, and
`/v1/chat/completions` is the request path: extract every piece of text,
pseudonymise all of it under one mapping, and forward only what came back.

The route fails closed. Anything the extraction seam cannot place stops the
request where it stands, before a byte reaches the provider -- a 502 returned
after forwarding would satisfy a status-code check and leak regardless.

Restoration of the answer is the next task; today the upstream body is
returned as it arrived, placeholders and all.
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from privaparse.app.config import Settings
from privaparse.app.logging import get_logger
from privaparse.engine import PrivaParseEngine
from privaparse.gateway.extract import UnscannableField, extract, write_back
from privaparse.gateway.upstream import Upstream

logger = get_logger(__name__)

_CHAT_PATH = "/v1/chat/completions"

# OpenAI's own endpoint. `Settings` has no field yet for pointing the gateway
# at a different provider (Azure, a local vLLM server, ...); that operator
# knob belongs with `privaparse serve`, which is what actually constructs a
# production `Upstream`. Every test injects its own fake, so this default is
# only ever reached by a real deployment.
_DEFAULT_UPSTREAM_BASE_URL = "https://api.openai.com"


def _error(status: int, message: str, kind: str) -> JSONResponse:
    """An OpenAI-shaped error, so a client's own error handling still works."""
    return JSONResponse({"error": {"message": message, "type": kind}}, status_code=status)


def create_app(
    settings: Settings,
    *,
    engine: PrivaParseEngine | None = None,
    upstream: Upstream | None = None,
) -> Starlette:
    """Build the gateway ASGI app.

    `engine` and `upstream` are injectable so the test suite needs neither a
    model nor a network; a production caller passes only `settings` and both
    are built for real.
    """
    engine = engine if engine is not None else PrivaParseEngine(settings)
    upstream = upstream if upstream is not None else Upstream(_DEFAULT_UPSTREAM_BASE_URL)

    async def healthz(request: Request) -> JSONResponse:
        # "ready" means the engine exists: settings validated, vault database
        # open. It does not mean the detector has loaded its model -- that
        # load is lazy (PrivaParseEngine.detector), and forcing it here would
        # turn a liveness probe into a 1.2 GB download on the first check.
        return JSONResponse({"status": "ready"})

    async def list_models(request: Request) -> JSONResponse:
        status, body, _headers = await upstream.get_json("/v1/models", request.headers)
        return JSONResponse(body, status_code=status)

    async def chat_completions(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "the request body is not valid JSON", "invalid_request_error")

        if body.get("stream") if isinstance(body, dict) else False:
            # A streamed answer needs hold-back restoration, which is a later
            # task. Forwarding now would pseudonymise correctly and then hand
            # back an answer full of placeholders: a partial success that
            # reads as a working gateway and hides the gap.
            return _error(
                501,
                "streaming is not restored yet, so this gateway will not forward "
                "a streaming request",
                "not_implemented",
            )

        try:
            nodes = extract(body)
        except UnscannableField as refusal:
            # Fail closed. The pointer is logged and returned; the value that
            # tripped it is in neither, which is the whole reason
            # UnscannableField carries a pointer instead of a payload.
            logger.warning("refused a request: %s", refusal)
            return _error(
                502,
                f"privaparse cannot scan this request and will not forward it: {refusal}",
                "privaparse_unscannable_field",
            )

        outbound = body
        if nodes:
            # One batch, one mapping. Node by node would open a session per
            # node, and the answer -- which mixes placeholders from all of
            # them -- could not be reversed against any single one.
            # `pseudonymize_batch` runs the detector and hits the vault, both
            # blocking, so it goes to a worker thread rather than stalling
            # every other request on the loop.
            batch = await run_in_threadpool(
                engine.pseudonymize_batch, [node.text for node in nodes]
            )
            outbound = write_back(body, nodes, batch.texts)

        status, reply, _headers = await upstream.post_json(
            _CHAT_PATH, outbound, request.headers
        )
        # Restoration lands in the next task; until then the answer goes back
        # exactly as the provider sent it, placeholders visible.
        return JSONResponse(reply, status_code=status)

    app = Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/v1/models", list_models, methods=["GET"]),
            Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        ],
    )
    # Later tasks read these off app.state (Task 3 needs both: the engine to
    # pseudonymise, the upstream to forward). Nothing from the request itself
    # is ever stored here -- see test_the_gateway_stores_no_credential.
    app.state.engine = engine
    app.state.upstream = upstream
    return app
