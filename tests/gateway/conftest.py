from __future__ import annotations

import json

import pytest


class FakeUpstream:
    """Stands in for the provider. Records every request it was handed."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        self.reply: dict = {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "ok"},
                 "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        self.chunks: list[bytes] = []

    async def post_json(self, path, body, headers):
        self.requests.append(json.loads(json.dumps(body)))
        self.headers.append(dict(headers))
        return 200, self.reply, {"content-type": "application/json"}

    async def get_json(self, path, headers):
        # No request body on a GET, so nothing is appended to `requests` --
        # `.last` stays meaningful for the POST endpoints later tasks add.
        self.headers.append(dict(headers))
        return 200, self.reply, {"content-type": "application/json"}

    async def stream(self, path, body, headers):
        self.requests.append(json.loads(json.dumps(body)))
        self.headers.append(dict(headers))
        for chunk in self.chunks:
            yield chunk

    @property
    def last(self) -> dict:
        return self.requests[-1]


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream()
