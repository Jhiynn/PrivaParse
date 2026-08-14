"""PrivaParse's own capabilities over HTTP.

These routes are not a proxy. They expose pseudonymisation, restoration and
detection directly, so the tool is usable from a shell script or another
language without an LLM in the picture.

Everything here is a thin adapter over a `PrivaParseEngine` method that already
exists and is tested. No detection or restoration logic lives in this file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from privaparse.gateway.errors import _error
from privaparse.parser.detector import GlinerUnavailableError

if TYPE_CHECKING:
    from privaparse.engine import PrivaParseEngine
    from privaparse.gateway.cache import CachingDetector
    from privaparse.parser.types import Span

DETECT_PATH = "/privaparse/detect"
PSEUDONYMIZE_PATH = "/privaparse/pseudonymize"
REVERSE_PATH = "/privaparse/reverse"
CATALOGUE_PATH = "/privaparse/catalogue"
VAULT_PATH = "/privaparse/vault"


class _BadRequest(ValueError):
    """A caller's fault, carrying the message they should see."""


def _require_object(body: Any) -> dict[str, Any]:
    """The body, once confirmed to be a JSON object.

    Split out so every route that inspects the body -- not just the two that
    accept `text`/`texts` -- reports the same message for the same mistake,
    rather than each route's own field checks improvising one.
    """
    if not isinstance(body, dict):
        raise _BadRequest("the request body must be a JSON object")
    return body


def _texts_from(body: Any) -> tuple[list[str], bool]:
    """The texts to work on, and whether the caller used the singular form.

    Accepting both `text` and `texts` costs one branch here and saves every
    caller from remembering which of two sibling routes takes which.
    """
    body = _require_object(body)
    if "texts" in body:
        texts = body["texts"]
        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            raise _BadRequest("`texts` must be an array of strings")
        return texts, False
    if "text" in body:
        text = body["text"]
        if not isinstance(text, str):
            raise _BadRequest("`text` must be a string")
        return [text], True
    raise _BadRequest("provide either `text` or `texts`")


def _span_json(span: Span) -> dict[str, Any]:
    return {
        "start": span.start,
        "end": span.end,
        "text": span.text,
        "type": span.type,
        "score": span.score,
        "source": span.source,
        "label": span.label,
    }


def _resolved_span_json(resolved: Any) -> dict[str, Any]:
    payload = _span_json(resolved.span)
    payload["placeholder"] = resolved.placeholder
    payload["entity_id"] = resolved.entity_id
    return payload


async def _read_json(request: Request) -> Any:
    try:
        return await request.json()
    except Exception as exc:
        raise _BadRequest("the request body must be valid JSON") from exc


def direct_routes(engine: PrivaParseEngine, detector: CachingDetector) -> list[Route]:
    """The direct API's routes, bound to one engine and one detection cache."""

    async def detect(request: Request) -> JSONResponse:
        try:
            body = await _read_json(request)
            texts, singular = _texts_from(body)
        except _BadRequest as bad:
            return _error(400, str(bad), "invalid_request_error")

        try:
            # Runs the full pipeline -- masking, threshold, merge, coreference
            # -- through the same caching detector the proxy uses, not the
            # raw detector: see PrivaParseEngine.detect_many. Detection and
            # merging both block, so this goes to a worker thread rather than
            # stalling the loop the proxy's streaming routes also run on.
            found = await run_in_threadpool(
                lambda: engine.detect_many(texts, detector=detector)
            )
        except GlinerUnavailableError as exc:
            # Same shaping as the proxy's handling of this in server.py: a
            # genuine server-side misconfiguration, not a transient failure,
            # so 500 -- and the OpenAI-shaped envelope carries the install
            # guidance rather than a bare "Internal Server Error".
            return _error(500, str(exc), "privaparse_model_unavailable")
        payload = [[_span_json(s) for s in spans] for spans in found]
        return JSONResponse({"detections": payload[0] if singular else payload})

    async def pseudonymize(request: Request) -> JSONResponse:
        # Deferred like every other parser import in this file (see reverse()
        # below and PrivaParseEngine's own methods): pulling in the resolver
        # / normalizer / validator chain only when a request actually needs
        # it, not at module import time.
        from privaparse.parser.pseudonymizer import AlreadyPseudonymizedError

        try:
            body = await _read_json(request)
            texts, singular = _texts_from(body)
        except _BadRequest as bad:
            return _error(400, str(bad), "invalid_request_error")

        source_name = body.get("source_name")
        if source_name is not None and not isinstance(source_name, str):
            return _error(400, "`source_name` must be a string", "invalid_request_error")

        include_spans = body.get("include_spans", False)
        if not isinstance(include_spans, bool):
            return _error(
                400, "`include_spans` must be a boolean", "invalid_request_error"
            )

        try:
            # pseudonymize_batch runs the detector and hits the vault, both
            # blocking -- see the matching comment in server.py -- so this
            # goes to a worker thread rather than stalling every other
            # request on the loop.
            result = await run_in_threadpool(
                lambda: engine.pseudonymize_batch(
                    texts, source_name=source_name, detector=detector
                )
            )
        except GlinerUnavailableError as exc:
            return _error(500, str(exc), "privaparse_model_unavailable")
        except AlreadyPseudonymizedError as exc:
            return _error(400, str(exc), "invalid_request_error")

        # Fixed key, collapsing shape -- matches detect()'s "detections":
        # a key that changes name breaks callers the moment they switch
        # from one text to several, while a key whose value collapses is
        # something they handle once, at the point they already know
        # which form they sent. Do not swap this back to text/texts.
        payload: dict[str, Any] = {"mapping_id": result.mapping_id}
        payload["texts"] = result.texts[0] if singular else result.texts
        if include_spans:
            spans = [[_resolved_span_json(r) for r in group] for group in result.spans]
            payload["spans"] = spans[0] if singular else spans
        return JSONResponse(payload)

    async def reverse(request: Request) -> JSONResponse:
        from privaparse.parser.reverse_mapper import ForeignPlaceholderError, find_mapping_for

        try:
            body = await _read_json(request)
            body = _require_object(body)
        except _BadRequest as bad:
            return _error(400, str(bad), "invalid_request_error")
        if not isinstance(body.get("text"), str):
            return _error(400, "`text` must be a string", "invalid_request_error")

        text = body["text"]
        mapping_id = body.get("mapping_id")
        if mapping_id is not None and not isinstance(mapping_id, str):
            return _error(400, "`mapping_id` must be a string", "invalid_request_error")
        if not mapping_id:
            # Falsy and absent have to mean the same thing here:
            # engine.reverse already treats an empty string as "look it up"
            # (`mapping_id or find_mapping_for(...)`), and echoing "" back as
            # the mapping_id would hand the caller a value they can never use.
            mapping_id = None

        strict = body.get("strict", False)
        if not isinstance(strict, bool):
            return _error(400, "`strict` must be a boolean", "invalid_request_error")

        def _do_reverse() -> tuple[str, Any]:
            # ReverseResult carries no mapping_id, so the caller-visible id
            # has to come from the same discovery engine.reverse would do
            # internally -- resolved here so it can be reported, then handed
            # to engine.reverse so it does not have to repeat the lookup.
            # Both the lookup and the restore hit the vault, so both run in
            # this one worker-thread call rather than stalling the loop twice.
            resolved = mapping_id
            if resolved is None:
                with engine.database.session() as session:
                    resolved = find_mapping_for(text, repo=engine.repository(session))
            return resolved, engine.reverse(resolved, text, strict=strict)

        try:
            resolved_mapping_id, result = await run_in_threadpool(_do_reverse)
        except ForeignPlaceholderError as exc:
            return _error(400, str(exc), "invalid_request_error")
        except LookupError as missing:
            # UnknownMappingError subclasses KeyError, whose __str__ wraps the
            # message in repr()'s quotes; args[0] is the constructor's own
            # message underneath, which is what a caller should actually see.
            # NoCoveringMappingError is a plain LookupError, where args[0]
            # already agrees with str(missing).
            detail = missing.args[0] if missing.args else str(missing)
            return _error(404, str(detail), "mapping_not_found")

        return JSONResponse(
            {
                "text": result.text,
                "mapping_id": resolved_mapping_id,
                "restored": result.restored,
                "recovered": result.recovered,
                "foreign": result.foreign,
                "unknown": result.unknown,
            }
        )

    async def catalogue(request: Request) -> JSONResponse:
        book = engine.catalogue
        return JSONResponse(
            {
                "version": book.version,
                "types": [
                    {
                        "name": t.name,
                        "enabled": t.enabled,
                        "reversible": t.reversible,
                        "threshold": t.threshold,
                        "labels": list(t.labels),
                        "validator": t.validator,
                    }
                    for t in book.enabled
                ],
            }
        )

    async def vault(request: Request) -> JSONResponse:
        stats = await run_in_threadpool(engine.vault_stats)
        return JSONResponse(
            {
                "mappings": stats.mappings,
                "entities": stats.entities,
                "surface_forms": stats.surface_forms,
                "by_type": dict(stats.by_type),
            }
        )

    return [
        Route(DETECT_PATH, detect, methods=["POST"]),
        Route(PSEUDONYMIZE_PATH, pseudonymize, methods=["POST"]),
        Route(REVERSE_PATH, reverse, methods=["POST"]),
        Route(CATALOGUE_PATH, catalogue, methods=["GET"]),
        Route(VAULT_PATH, vault, methods=["GET"]),
    ]
