"""Shared-key authentication for the gateway.

A pure ASGI middleware rather than a `BaseHTTPMiddleware`. The latter wraps
the response object, and this gateway streams completions -- wrapping a
`StreamingResponse` is a well-known way to break it. This one either refuses
before the app runs or steps out of the way entirely, so it never touches a
response body.
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING

from starlette.datastructures import Headers

from privaparse.gateway.errors import _error

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

#: Deliberately not `Authorization`: `upstream.py` forwards that header to the
#: provider, so this key would either break proxying or be sent to OpenAI.
HEADER = "x-privaparse-key"


class ApiKeyMiddleware:
    """Rejects requests that do not present the configured key.

    No key configured means no authentication, which is the default and is
    correct on loopback, where reachability is the access control.
    """

    def __init__(self, app: ASGIApp, *, api_key: str, exempt: frozenset[str]) -> None:
        self.app = app
        self._api_key = api_key
        self._exempt = exempt

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # `scope["type"] != "http"` also exempts websocket and lifespan
        # scopes. There is no WebSocketRoute anywhere in this app today, so
        # this is latent rather than live -- but whoever adds one should know
        # it would arrive here unauthenticated.
        if scope["type"] != "http" or not self._api_key or scope["path"] in self._exempt:
            await self.app(scope, receive, send)
            return

        presented = Headers(scope=scope).get(HEADER, "")
        # compare_digest, not ==, so a wrong key does not leak the right one
        # one character at a time to anyone who can measure the reply.
        #
        # Both sides go to bytes first: compare_digest raises TypeError on a
        # `str` operand that is not ASCII-only, and Starlette decodes header
        # bytes through latin-1 (Headers.__getitem__), which never fails --
        # so a single raw byte above 0x7f in the header would otherwise
        # surface as an unhandled 500 instead of the ordinary 401 every other
        # bad credential gets. `presented` is re-encoded with latin-1, the
        # exact inverse of that decode, so it round-trips the raw bytes the
        # client sent losslessly and can never raise. `_api_key` is a normal
        # operator-configured string, encoded as UTF-8 -- also incapable of
        # raising, for any string Python can represent.
        if not presented or not hmac.compare_digest(
            presented.encode("latin-1"), self._api_key.encode("utf-8")
        ):
            response = _error(
                401,
                "this gateway requires a key. Present it as the X-PrivaParse-Key header.",
                "invalid_api_key",
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
