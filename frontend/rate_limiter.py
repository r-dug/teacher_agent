"""
Token-bucket rate limiter, keyed by session_id.

Each session gets its own bucket.  The bucket refills continuously at
`refill_rate` tokens per second up to `capacity`.  A single request costs
1 token by default.

Audio input events cost more (they trigger expensive STT + LLM + TTS work),
while lightweight events (ping, status queries) cost less.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    capacity: float
    refill_rate: float          # tokens per second
    _tokens: float = field(init=False)
    _last: float = field(init=False)

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._last = time.monotonic()

    def consume(self, tokens: float = 1.0) -> bool:
        now = time.monotonic()
        self._tokens = min(
            self.capacity,
            self._tokens + (now - self._last) * self.refill_rate,
        )
        self._last = now
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False


class RateLimiter:
    """Per-key token-bucket rate limiter."""

    def __init__(self, capacity: float = 60.0, refill_rate: float = 1.0) -> None:
        self._capacity = capacity
        self._refill_rate = refill_rate
        self._buckets: dict[str, _Bucket] = {}

    def allow(self, key: str, tokens: float = 1.0) -> bool:
        """Return True if the request is permitted; False if throttled."""
        if key not in self._buckets:
            self._buckets[key] = _Bucket(self._capacity, self._refill_rate)
        return self._buckets[key].consume(tokens)

    def remove(self, key: str) -> None:
        """Remove a bucket when the session ends (memory hygiene)."""
        self._buckets.pop(key, None)

    def __len__(self) -> int:
        return len(self._buckets)


# Module-level singleton
limiter = RateLimiter()
