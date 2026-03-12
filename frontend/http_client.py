"""Shared async HTTP client for backend REST calls."""

from __future__ import annotations

import httpx

_client: httpx.AsyncClient | None = None


async def init(base_url: str, timeout: float = 30.0) -> None:
    global _client
    _client = httpx.AsyncClient(base_url=base_url, timeout=timeout)


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def get() -> httpx.AsyncClient:
    assert _client is not None, "HTTP client not initialised"
    return _client
