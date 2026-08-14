"""The gateway's shared error envelope.

Split out from `server.py` so `direct.py` can build the same OpenAI-shaped
error without a circular import: `server.py` mounts `direct.py`'s routes, so
a module-level import in the other direction would not work. This module
depends on neither, so both import it normally.
"""

from __future__ import annotations

from starlette.responses import JSONResponse


def _error(status: int, message: str, kind: str) -> JSONResponse:
    """An OpenAI-shaped error, so a client's own error handling still works."""
    return JSONResponse({"error": {"message": message, "type": kind}}, status_code=status)
