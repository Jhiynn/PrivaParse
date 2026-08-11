"""OpenAI-compatible local gateway.

Pseudonymises a request on the way out, restores the answer on the way back.
Nothing here replaces the CLI or its engine -- it is the same pipeline,
reached over the wire, so a client that only knows how to speak the OpenAI
protocol gets PrivaParse without changing a line of its own code.
"""

from __future__ import annotations

from privaparse.gateway.server import create_app

__all__ = ["create_app"]
