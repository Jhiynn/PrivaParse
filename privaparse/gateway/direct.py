"""PrivaParse's own capabilities over HTTP.

These routes are not a proxy. They expose pseudonymisation, restoration and
detection directly, so the tool is usable from a shell script or another
language without an LLM in the picture.

Everything here is a thin adapter over a `PrivaParseEngine` method that already
exists and is tested. No detection or restoration logic lives in this file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from privaparse.gateway.errors import _error

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


def _texts_from(body: Any) -> tuple[list[str], bool]:
    """The texts to work on, and whether the caller used the singular form.

    Accepting both `text` and `texts` costs one branch here and saves every
    caller from remembering which of two sibling routes takes which.
    """
    if not isinstance(body, dict):
        raise _BadRequest("the request body must be a JSON object")
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

        found = detector.detect_many(texts)
        payload = [[_span_json(s) for s in spans] for spans in found]
        return JSONResponse({"detections": payload[0] if singular else payload})

    async def pseudonymize(request: Request) -> JSONResponse:
        try:
            body = await _read_json(request)
            texts, singular = _texts_from(body)
        except _BadRequest as bad:
            return _error(400, str(bad), "invalid_request_error")

        source_name = body.get("source_name")
        if source_name is not None and not isinstance(source_name, str):
            return _error(400, "`source_name` must be a string", "invalid_request_error")

        result = engine.pseudonymize_batch(
            texts, source_name=source_name, detector=detector
        )

        payload: dict[str, Any] = {"mapping_id": result.mapping_id}
        payload["text" if singular else "texts"] = (
            result.texts[0] if singular else result.texts
        )
        if body.get("include_spans") is True:
            spans = [[_resolved_span_json(r) for r in group] for group in result.spans]
            payload["spans"] = spans[0] if singular else spans
        return JSONResponse(payload)

    async def reverse(request: Request) -> JSONResponse:
        try:
            body = await _read_json(request)
        except _BadRequest as bad:
            return _error(400, str(bad), "invalid_request_error")
        if not isinstance(body, dict) or not isinstance(body.get("text"), str):
            return _error(400, "`text` must be a string", "invalid_request_error")

        text = body["text"]
        mapping_id = body.get("mapping_id")
        if mapping_id is not None and not isinstance(mapping_id, str):
            return _error(400, "`mapping_id` must be a string", "invalid_request_error")

        try:
            if mapping_id is None:
                # ReverseResult carries no mapping_id, so the caller-visible id
                # has to come from the same discovery engine.reverse would do
                # internally -- resolved here so it can be reported, then
                # handed to engine.reverse so it does not have to repeat the
                # lookup.
                from privaparse.parser.reverse_mapper import find_mapping_for

                with engine.database.session() as session:
                    mapping_id = find_mapping_for(text, repo=engine.repository(session))
            result = engine.reverse(
                mapping_id, text, strict=bool(body.get("strict", False))
            )
        except LookupError as missing:
            return _error(404, str(missing), "mapping_not_found")

        return JSONResponse(
            {
                "text": result.text,
                "mapping_id": mapping_id,
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
        stats = engine.vault_stats()
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
