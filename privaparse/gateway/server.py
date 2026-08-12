"""Starlette app: the process the rest of the gateway attaches to.

`/healthz` proves the process is up, `/v1/models` proxies untouched, and
`/v1/chat/completions` is the request path: extract every piece of text,
pseudonymise all of it under one mapping, and forward only what came back.

The route fails closed. Anything the extraction seam cannot place stops the
request where it stands, before a byte reaches the provider -- a 502 returned
after forwarding would satisfy a status-code check and leak regardless.

The answer is restored on the way back, and that half never aborts: a failure
outbound risks disclosure, a failure inbound costs readability.

Detection results are cached per text block for the life of the process, which
is what keeps a twentieth chat turn from re-detecting nineteen unchanged
messages. Nothing else about a request is reused -- see `cache.py`.
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from privaparse.app.config import Settings
from privaparse.app.logging import get_logger
from privaparse.engine import PrivaParseEngine
from privaparse.gateway.cache import CachingDetector, DetectionCache
from privaparse.gateway.extract import (
    UnscannableField,
    extract,
    extract_response,
    write_back,
)
from privaparse.gateway.stream import max_placeholder_length, restore_sse
from privaparse.gateway.upstream import Upstream

logger = get_logger(__name__)

_CHAT_PATH = "/v1/chat/completions"

# OpenAI's own endpoint. `Settings` has no field yet for pointing the gateway
# at a different provider (Azure, a local vLLM server, ...); that operator
# knob belongs with `privaparse serve`, which is what actually constructs a
# production `Upstream`. Every test injects its own fake, so this default is
# only ever reached by a real deployment.
_DEFAULT_UPSTREAM_BASE_URL = "https://api.openai.com"


async def _restore(engine: PrivaParseEngine, mapping_id: str, reply: dict) -> dict:
    """Put the real values back into the provider's answer.

    Never raises. The request path fails closed because a failure there risks
    disclosure; here the answer already exists and has already been paid for,
    so a failure costs readability and nothing else. The caller gets the
    answer with its placeholders standing, and the reason goes to the log.

    `usage` is deliberately untouched. The counts describe the pseudonymised
    text, which is the text the provider billed for; recomputing them against
    the restored answer would produce a number the invoice disagrees with.
    """
    try:
        nodes = extract_response(reply)
        if not nodes:
            return reply
        restored = await run_in_threadpool(
            lambda: [engine.reverse(mapping_id, node.text).text for node in nodes]
        )
        return write_back(reply, nodes, restored)
    except Exception:  # noqa: BLE001 - see the docstring: nothing here may abort
        # No payload in the message. What failed to restore is by definition
        # the part of the answer that concerns a person.
        logger.warning(
            "could not restore an answer; returning it with placeholders standing",
            exc_info=False,
        )
        return reply


def _stream_restorer(engine: PrivaParseEngine, mapping_id: str):
    """A `restore(text) -> text` for `restore_sse`, scoped to this request.

    The vault lookup is blocking, so it goes to a worker thread; a streamed
    answer arrives in many small pieces and doing it inline would block the
    event loop once per piece. Failures are not caught here -- `restore_sse`
    owns that rule, and catching it twice would hide which layer gave up.
    """

    async def restore(text: str) -> str:
        return await run_in_threadpool(lambda: engine.reverse(mapping_id, text).text)

    return restore


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
    # One cache for the process. Detection is the expensive half of the
    # request path and a chat client resends its whole history every turn, so
    # most blocks of any request but the first were detected already.
    cache = DetectionCache(settings.gateway_cache)
    detector = CachingDetector(engine, cache)

    async def healthz(request: Request) -> JSONResponse:
        # "ready" means the engine exists: settings validated, vault database
        # open. It does not mean the detector has loaded its model -- that
        # load is lazy (PrivaParseEngine.detector), and forcing it here would
        # turn a liveness probe into a 1.2 GB download on the first check.
        return JSONResponse({"status": "ready"})

    async def list_models(request: Request) -> JSONResponse:
        status, body, _headers = await upstream.get_json("/v1/models", request.headers)
        return JSONResponse(body, status_code=status)

    async def chat_completions(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "the request body is not valid JSON", "invalid_request_error")

        streaming = bool(body.get("stream")) if isinstance(body, dict) else False

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
        mapping_id: str | None = None
        if nodes:
            # One batch, one mapping. Node by node would open a session per
            # node, and the answer -- which mixes placeholders from all of
            # them -- could not be reversed against any single one.
            # `pseudonymize_batch` runs the detector and hits the vault, both
            # blocking, so it goes to a worker thread rather than stalling
            # every other request on the loop.
            batch = await run_in_threadpool(
                lambda: engine.pseudonymize_batch(
                    [node.text for node in nodes], detector=detector
                )
            )
            outbound = write_back(body, nodes, batch.texts)
            mapping_id = batch.mapping_id

        if streaming:
            relay = upstream.stream(_CHAT_PATH, outbound, request.headers)
            if mapping_id is not None:
                relay = restore_sse(
                    relay,
                    restore=_stream_restorer(engine, mapping_id),
                    max_hold=max_placeholder_length(engine.catalogue),
                )
            return StreamingResponse(relay, media_type="text/event-stream")

        status, reply, _headers = await upstream.post_json(
            _CHAT_PATH, outbound, request.headers
        )
        if mapping_id is not None:
            reply = await _restore(engine, mapping_id, reply)
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
    # `privaparse gateway stats` reports the hit rate off this. It holds
    # digests and spans, never a request body.
    app.state.cache = cache
    return app
