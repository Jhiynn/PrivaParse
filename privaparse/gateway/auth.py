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
        if scope["type"] != "http" or not self._api_key or scope["path"] in self._exempt:
            await self.app(scope, receive, send)
            return

        presented = Headers(scope=scope).get(HEADER, "")
        # compare_digest, not ==, so a wrong key does not leak the right one
        # one character at a time to anyone who can measure the reply.
        if not presented or not hmac.compare_digest(presented, self._api_key):
            response = _error(
                401,
                "this gateway requires a key. Present it as the X-PrivaParse-Key header.",
                "invalid_api_key",
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
