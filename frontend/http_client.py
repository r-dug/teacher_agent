"""Shared async HTTP client for backend REST calls."""

from __future__ import annotations

import httpx

_client: httpx.AsyncClient | None = None


async def init(base_url: str, timeout: float = 30.0, secret: str | None = None) -> None:
    global _client
    headers = {"X-Internal-Token": secret} if secret else {}
    _client = httpx.AsyncClient(base_url=base_url, timeout=timeout, headers=headers)


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def get() -> httpx.AsyncClient:
    assert _client is not None, "HTTP client not initialised"
    return _client
