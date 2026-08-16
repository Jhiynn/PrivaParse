"""Starlette app: the process the rest of the gateway attaches to.

`/healthz` proves the process is up, `/v1/models` proxies untouched, and
`/v1/chat/completions` is the request path: extract every piece of text,
pseudonymise all of it under one mapping, and forward only what came back.

The route fails closed. Anything an adapter's request walk cannot place stops
the request where it stands, before a byte reaches the provider -- a 502
returned after forwarding would satisfy a status-code check and leak
regardless.

The answer is restored on the way back, and that half never aborts: a failure
outbound risks disclosure, a failure inbound costs readability.

Detection results are cached per text block for the life of the process, which
is what keeps a twentieth chat turn from re-detecting nineteen unchanged
messages. Nothing else about a request is reused -- see `cache.py`.
"""

from __future__ import annotations

import time

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from privaparse.app.config import Settings
from privaparse.app.logging import get_logger
from privaparse.engine import PrivaParseEngine
from privaparse.gateway.adapter import openai as shape
from privaparse.gateway.adapter import responses as responses_shape
from privaparse.gateway.auth import ApiKeyMiddleware
from privaparse.gateway.cache import CachingDetector, DetectionCache
from privaparse.gateway.direct import direct_routes
from privaparse.gateway.errors import (
    detection_unavailable,
    malformed_body,
    unscannable,
)
from privaparse.gateway.extract import UnscannableField, write_back
from privaparse.gateway.metrics import Metrics
from privaparse.gateway.stream import max_placeholder_length, restore_sse
from privaparse.gateway.stream_responses import restore_responses_sse
from privaparse.gateway.upstream import Upstream
from privaparse.parser.detector import GlinerUnavailableError

logger = get_logger(__name__)

_CHAT_PATH = "/v1/chat/completions"

#: The Responses API. Codex CLI speaks only this one -- `wire_api = "chat"`
#: was removed in February 2026 -- so a Chat Completions gateway cannot serve
#: it at all.
RESPONSES_PATH = "/v1/responses"

#: Namespaced so it can never collide with a path the provider defines.
STATS_PATH = "/privaparse/stats"



async def _restore(
    engine: PrivaParseEngine,
    mapping_id: str,
    reply: dict,
    *,
    fuzzy: bool = False,
    walk=shape.extract_answer,
) -> dict:
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
        nodes = walk(reply)
        if not nodes:
            return reply
        restored = await run_in_threadpool(
            lambda: [engine.reverse(mapping_id, node.text, fuzzy=fuzzy).text for node in nodes]
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


def _stream_restorer(engine: PrivaParseEngine, mapping_id: str, *, fuzzy: bool = False):
    """A `restore(text) -> text` for `restore_sse`, scoped to this request.

    The vault lookup is blocking, so it goes to a worker thread; a streamed
    answer arrives in many small pieces and doing it inline would block the
    event loop once per piece. Failures are not caught here -- `restore_sse`
    owns that rule, and catching it twice would hide which layer gave up.
    """

    async def restore(text: str) -> str:
        return await run_in_threadpool(
            lambda: engine.reverse(mapping_id, text, fuzzy=fuzzy).text
        )

    return restore


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

    Binding this app to anything but loopback is the caller's decision, not
    this function's -- `privaparse serve` is the one place that owns
    `_check_bind_address`, and a library user who calls this directly and
    serves it with uvicorn on `0.0.0.0` walks past that guard entirely. An
    empty `settings.api_key` is correct and expected on loopback, so this
    does not refuse to build the app -- it only says, once, what the caller
    is choosing.
    """
    if not settings.api_key:
        logger.warning(
            "no PRIVAPARSE_API_KEY is configured: this app authenticates "
            "nothing. Bind it to loopback only, or set an api_key before "
            "serving it anywhere else can reach it."
        )
    engine = engine if engine is not None else PrivaParseEngine(settings)
    # `settings.gateway_upstream` is the operator's knob for pointing at Azure
    # or a local vLLM server. Every test injects its own fake, so the real
    # client here is only ever built by a real deployment.
    upstream = upstream if upstream is not None else Upstream(settings.gateway_upstream)
    # One cache for the process. Detection is the expensive half of the
    # request path and a chat client resends its whole history every turn, so
    # most blocks of any request but the first were detected already.
    cache = DetectionCache(settings.gateway_cache)
    detector = CachingDetector(engine, cache)
    metrics = Metrics()
    # The direct API: PrivaParse's own capabilities over HTTP, not a proxy.
    # Bound to the same engine and detection cache as the request path above.
    direct = direct_routes(engine, detector)

    async def healthz(request: Request) -> JSONResponse:
        # "ready" means the engine exists: settings validated, vault database
        # open. It does not mean the detector has loaded its model -- that
        # load is lazy (PrivaParseEngine.detector), and forcing it here would
        # turn a liveness probe into a 1.2 GB download on the first check.
        return JSONResponse({"status": "ready"})

    async def stats(request: Request) -> JSONResponse:
        # Numbers only. See metrics.py for why there is no per-type breakdown.
        return JSONResponse(metrics.snapshot(cache))

    async def list_models(request: Request) -> JSONResponse:
        status, body, _headers = await upstream.get_json("/v1/models", request.headers)
        return JSONResponse(body, status_code=status)

    async def chat_completions(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return malformed_body()

        streaming = bool(body.get("stream")) if isinstance(body, dict) else False

        try:
            nodes = shape.extract_request(body)
        except UnscannableField as refusal:
            # Fail closed. The pointer is logged and returned; the value that
            # tripped it is in neither, which is the whole reason
            # UnscannableField carries a pointer instead of a payload.
            logger.warning("refused a request: %s", refusal)
            return unscannable(refusal)

        started = time.perf_counter()
        outbound = body
        mapping_id: str | None = None
        entities = 0
        if nodes:
            # One batch, one mapping. Node by node would open a mapping per
            # node, and the answer -- which mixes placeholders from all of
            # them -- could not be reversed against any single one.
            # `pseudonymize_batch` runs the detector and hits the vault, both
            # blocking, so it goes to a worker thread rather than stalling
            # every other request on the loop.
            try:
                batch = await run_in_threadpool(
                    lambda: engine.pseudonymize_batch(
                        [node.text for node in nodes],
                        detector=detector,
                        # A chat client replays its history, so a placeholder
                        # that survived unrestored comes back in. Refusing it
                        # would turn one restoration miss into a conversation
                        # that can never recover -- see pseudonymize_batch.
                        adopt_placeholders=True,
                    )
                )
            except GlinerUnavailableError as exc:
                logger.error("detection is unavailable: %s", exc)
                return detection_unavailable(exc)
            outbound = write_back(body, nodes, batch.texts)
            mapping_id = batch.mapping_id
            entities = len(batch.placeholders)
            if settings.gateway_hint and entities:
                # After write_back on purpose: the hint never reaches the
                # detector, so it cannot be scanned or stored as an entity.
                # Only when something was actually replaced -- otherwise the
                # caller pays tokens for a request with nothing to protect.
                outbound = shape.with_placeholder_hint(outbound)

        # Recorded here rather than when the answer lands: this is PrivaParse's
        # own share of the request, which is the part an operator can act on.
        # A refusal above never reaches this line, so a 502 does not enter the
        # average as a request that carried no entities.
        metrics.record(entities=entities, seconds=time.perf_counter() - started)

        if streaming:
            relay = upstream.stream(_CHAT_PATH, outbound, request.headers)
            if mapping_id is not None:
                relay = restore_sse(
                    relay,
                    restore=_stream_restorer(
                        engine, mapping_id, fuzzy=settings.gateway_fuzzy
                    ),
                    max_hold=max_placeholder_length(engine.catalogue),
                    # A mangled placeholder is only restorable if it arrives in
                    # one piece, and the strict hold-back only ever protects
                    # `[[`. Widening it is what lets the tolerant matcher see
                    # `[PERSON_A1]` whole instead of split across two events.
                    lenient=settings.gateway_fuzzy,
                )
            return StreamingResponse(relay, media_type="text/event-stream")

        status, reply, _headers = await upstream.post_json(
            _CHAT_PATH, outbound, request.headers
        )
        if mapping_id is not None:
            reply = await _restore(engine, mapping_id, reply, fuzzy=settings.gateway_fuzzy)
        return JSONResponse(reply, status_code=status)

    async def responses_endpoint(request: Request) -> Response:
        """The Responses API, which is the only protocol Codex CLI speaks.

        Same rules as the chat route -- one mapping per request, fail closed
        outbound, never abort inbound -- over a different shape.
        """
        try:
            body = await request.json()
        except ValueError:
            return malformed_body()

        streaming = bool(body.get("stream")) if isinstance(body, dict) else False

        try:
            nodes = responses_shape.extract_request(
                body, allow_images=settings.gateway_allow_images
            )
        except UnscannableField as refusal:
            logger.warning("refused a responses request: %s", refusal)
            return unscannable(refusal)

        started = time.perf_counter()
        outbound = body
        mapping_id: str | None = None
        entities = 0
        if nodes:
            try:
                batch = await run_in_threadpool(
                    lambda: engine.pseudonymize_batch(
                        [node.text for node in nodes],
                        detector=detector,
                        # A chat client replays its history, so a placeholder
                        # that survived unrestored comes back in. Refusing it
                        # would turn one restoration miss into a conversation
                        # that can never recover -- see pseudonymize_batch.
                        adopt_placeholders=True,
                    )
                )
            except GlinerUnavailableError as exc:
                logger.error("detection is unavailable: %s", exc)
                return detection_unavailable(exc)
            outbound = write_back(body, nodes, batch.texts)
            mapping_id = batch.mapping_id
            entities = len(batch.placeholders)
            if settings.gateway_hint and entities:
                outbound = responses_shape.with_placeholder_hint(outbound)

        metrics.record(entities=entities, seconds=time.perf_counter() - started)

        if streaming:
            relay = upstream.stream(RESPONSES_PATH, outbound, request.headers)
            if mapping_id is not None:
                relay = restore_responses_sse(
                    relay,
                    restore=_stream_restorer(
                        engine, mapping_id, fuzzy=settings.gateway_fuzzy
                    ),
                    max_hold=max_placeholder_length(engine.catalogue),
                    lenient=settings.gateway_fuzzy,
                )
            return StreamingResponse(relay, media_type="text/event-stream")

        status, reply, _headers = await upstream.post_json(
            RESPONSES_PATH, outbound, request.headers
        )
        if mapping_id is not None:
            reply = await _restore(
                engine,
                mapping_id,
                reply,
                fuzzy=settings.gateway_fuzzy,
                walk=responses_shape.extract_answer,
            )
        return JSONResponse(reply, status_code=status)

    app = Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route(RESPONSES_PATH, responses_endpoint, methods=["POST"]),
            Route(STATS_PATH, stats, methods=["GET"]),
            Route("/v1/models", list_models, methods=["GET"]),
            Route("/v1/chat/completions", chat_completions, methods=["POST"]),
            *direct,
        ],
        # Added via the constructor argument, not by wrapping the returned
        # app, so `create_app(...)` keeps returning the Starlette instance
        # itself -- `create_app(...).state` is read by both a test and Task
        # 3, and wrapping the app in another object here would break it.
        middleware=[
            Middleware(
                ApiKeyMiddleware,
                api_key=settings.api_key,
                exempt=frozenset({"/healthz"}),
            )
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
