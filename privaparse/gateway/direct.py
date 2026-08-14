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

    return [Route(DETECT_PATH, detect, methods=["POST"])]
