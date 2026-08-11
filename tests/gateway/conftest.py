from __future__ import annotations

import copy
import json

import pytest


class FakeUpstream:
    """Stands in for the provider. Records every request it was handed."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        #: Set by a test that wants to see how the gateway handles a provider
        #: error -- a rate limit, an auth failure -- rather than a completion.
        self.status: int = 200
        #: When true, the reply's assistant content is whatever arrived in the
        #: first message. A real model repeats a placeholder back at you
        #: constantly -- it looks like a name to it -- and that is the only way
        #: a test can see a placeholder it could not have known in advance.
        self.echo: bool = False
        #: Optional callable(body) -> reply, for a test whose expected answer
        #: has to contain a placeholder only this request could have issued.
        self.reply_for = None
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
        reply = self.reply
        if self.reply_for is not None:
            reply = self.reply_for(body)
        elif self.echo:
            reply = copy.deepcopy(self.reply)
            reply["choices"][0]["message"]["content"] = body["messages"][0]["content"]
        return self.status, reply, {"content-type": "application/json"}

    async def get_json(self, path, headers):
        # No request body on a GET, so nothing is appended to `requests` --
        # `.last` stays meaningful for the POST endpoints later tasks add.
        self.headers.append(dict(headers))
        return self.status, self.reply, {"content-type": "application/json"}

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
