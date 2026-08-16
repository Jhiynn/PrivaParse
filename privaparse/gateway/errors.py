"""The gateway's error kinds, and the envelope it hands a caller for each.

Split out from `server.py` so `direct.py` can build the same OpenAI-shaped
error without a circular import: `server.py` mounts `direct.py`'s routes, so
a module-level import in the other direction would not work. This module
depends on neither, so both import it normally.

The kinds live here beside the envelope rather than as literals at the call
sites, because the `type` string is the part a client's own error handling
switches on -- a misspelling in one route is a silently different error to
every caller, and nothing in the type system would say so.

The three cases that recur across routes each get a constructor. That matters
most for the refusal: the sentence it shows a caller is the gateway's privacy
statement -- the promise that a request it cannot scan is not forwarded -- and
a promise stated in more than one place is a promise that can drift. Written
once here, every protocol adapter makes it identically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from privaparse.gateway.extract import UnscannableField
    from privaparse.parser.detector import GlinerUnavailableError

#: The caller's request is malformed. OpenAI's own kind, deliberately, so a
#: client that already handles it needs to learn nothing about PrivaParse.
INVALID_REQUEST = "invalid_request_error"

#: The presented key is missing or wrong. OpenAI's own kind, same reason.
INVALID_API_KEY = "invalid_api_key"

#: Detection is not available at all -- weights or the extra are missing.
#: A `privaparse_` prefix because no provider has an equivalent: this is the
#: gateway's own failure, not one it is relaying.
MODEL_UNAVAILABLE = "privaparse_model_unavailable"

#: The request carries text the gateway has no rule for, so it was not
#: forwarded. See `docs/adr/0002-gateway-extraction-fails-closed.md`.
UNSCANNABLE_FIELD = "privaparse_unscannable_field"

#: Restoration was asked for against a mapping the vault does not hold.
MAPPING_NOT_FOUND = "mapping_not_found"


def error(status: int, message: str, kind: str) -> JSONResponse:
    """An OpenAI-shaped error, so a client's own error handling still works."""
    return JSONResponse({"error": {"message": message, "type": kind}}, status_code=status)


def malformed_body() -> JSONResponse:
    """The request body did not parse as JSON, on any route."""
    return error(400, "the request body is not valid JSON", INVALID_REQUEST)


def unscannable(refusal: UnscannableField) -> JSONResponse:
    """The gateway will not forward a request it cannot scan.

    502 rather than 400: the request never reached the upstream, and that is
    what the status says. The pointer travels; the value that tripped the
    refusal does not, which is why `UnscannableField` carries the one and not
    the other -- interpolating it here is safe only because of that.
    """
    return error(
        502,
        f"privaparse cannot scan this request and will not forward it: {refusal}",
        UNSCANNABLE_FIELD,
    )


def detection_unavailable(exc: GlinerUnavailableError) -> JSONResponse:
    """Detection could not run, so nothing was pseudonymised and nothing sent.

    500 and not 503: this is a server-side misconfiguration only an operator
    can fix, and 503 invites an OpenAI-compatible client to retry a condition
    that will not resolve on its own. The message is the exception's own,
    which carries the install guidance.
    """
    return error(500, str(exc), MODEL_UNAVAILABLE)
