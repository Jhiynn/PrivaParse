"""Starlette app: the process every later task in the gateway plan attaches to.

Nothing here pseudonymises anything yet. `/healthz` proves the process is up,
`/v1/models` proves the upstream plumbing works end to end, and
`/v1/chat/completions` is a deliberate 501: forwarding it unpseudonymised
would be a hole that looks like progress, and the next task builds the thing
that decides what may leave the machine.
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from privaparse.app.config import Settings
from privaparse.engine import PrivaParseEngine
from privaparse.gateway.upstream import Upstream

# OpenAI's own endpoint. `Settings` has no field yet for pointing the gateway
# at a different provider (Azure, a local vLLM server, ...); that operator
# knob belongs with `privaparse serve`, which is what actually constructs a
# production `Upstream`. Every test injects its own fake, so this default is
# only ever reached by a real deployment.
_DEFAULT_UPSTREAM_BASE_URL = "https://api.openai.com"


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

    async def healthz(request: Request) -> JSONResponse:
        # "ready" means the engine exists: settings validated, vault database
        # open. It does not mean the detector has loaded its model -- that
        # load is lazy (PrivaParseEngine.detector), and forcing it here would
        # turn a liveness probe into a 1.2 GB download on the first check.
        return JSONResponse({"status": "ready"})

    async def list_models(request: Request) -> JSONResponse:
        status, body, _headers = await upstream.get_json("/v1/models", request.headers)
        return JSONResponse(body, status_code=status)

    async def chat_completions(request: Request) -> JSONResponse:
        # Nothing is pseudonymised yet, so nothing may be forwarded. A stub
        # that forwarded the request untouched would be a hole that looks
        # like progress rather than the fail-closed contract the design
        # calls for.
        return JSONResponse(
            {
                "error": {
                    "message": "not implemented yet: the next task adds "
                    "pseudonymisation before this route can forward anything",
                    "type": "not_implemented",
                }
            },
            status_code=501,
        )

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
    return app
