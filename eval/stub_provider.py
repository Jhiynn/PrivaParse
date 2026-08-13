"""A provider that echoes back whatever it was sent, for end-to-end runs.

Echoing is what makes the round trip checkable: the gateway forwards
placeholders, so an echo returns placeholders, and the caller only sees a real
value again if restoration worked. The streaming path emits one character per
event, which is the worst case for the hold-back buffer.

    python -m uvicorn eval.stub_provider:app --host 127.0.0.1 --port 9000

Then point a gateway at it:

    PRIVAPARSE_GATEWAY_UPSTREAM=http://127.0.0.1:9000 privaparse serve
"""

from __future__ import annotations

import json

from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route


def _chunk(delta: dict, finish: str | None = None) -> bytes:
    payload = {
        "id": "stub", "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload)}\n\n".encode()


async def chat(request):
    body = await request.json()
    content = body["messages"][-1]["content"]

    if body.get("stream"):
        async def events():
            for character in content:
                yield _chunk({"content": character})
            yield _chunk({}, finish="stop")
            yield b"data: [DONE]\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    return JSONResponse({
        "id": "stub", "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })


async def models(request):
    return JSONResponse({"object": "list", "data": [{"id": "stub-model"}]})


app = Starlette(routes=[
    Route("/v1/chat/completions", chat, methods=["POST"]),
    Route("/v1/models", models, methods=["GET"]),
])
