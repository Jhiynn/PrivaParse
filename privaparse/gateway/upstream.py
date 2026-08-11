"""The only place that talks to the provider.

Headers are forwarded by allow-list rather than passed through wholesale: a
header the gateway does not understand could carry routing or caching semantics
it has no way to reason about, and the failure would be silent.
"""

from __future__ import annotations

from typing import AsyncIterator

import httpx

_FORWARDED = ("authorization", "content-type", "openai-organization", "openai-project")


class Upstream:
    def __init__(self, base_url: str, timeout: float = 600.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    @staticmethod
    def _headers(incoming) -> dict[str, str]:
        return {k: v for k, v in incoming.items() if k.lower() in _FORWARDED}

    async def post_json(self, path, body, headers):
        response = await self._client.post(
            f"{self.base_url}{path}", json=body, headers=self._headers(headers)
        )
        return response.status_code, response.json(), dict(response.headers)

    async def get_json(self, path, headers):
        response = await self._client.get(
            f"{self.base_url}{path}", headers=self._headers(headers)
        )
        return response.status_code, response.json(), dict(response.headers)

    async def stream(self, path, body, headers) -> AsyncIterator[bytes]:
        async with self._client.stream(
            "POST", f"{self.base_url}{path}", json=body, headers=self._headers(headers)
        ) as response:
            async for chunk in response.aiter_bytes():
                yield chunk

    async def aclose(self) -> None:
        await self._client.aclose()
