"""
Token usage tracking across all Anthropic API calls.

Records input/output/cache tokens per call type and model.
Thread-safe; records come from background worker threads.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass

# Approximate pricing (USD per token) for known models.
# Source: Anthropic pricing page; update as pricing changes.
_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6":  {"input": 3e-6,    "output": 15e-6,   "cache_read": 0.30e-6,  "cache_write": 3.75e-6},
    "claude-opus-4-6":    {"input": 15e-6,   "output": 75e-6,   "cache_read": 1.50e-6,  "cache_write": 18.75e-6},
    "claude-haiku-4-5":   {"input": 0.25e-6, "output": 1.25e-6, "cache_read": 0.03e-6,  "cache_write": 0.30e-6},
}
_DEFAULT_PRICING = {"input": 3e-6, "output": 15e-6, "cache_read": 0.30e-6, "cache_write": 3.75e-6}


def _price(model: str, inp: int, out: int, cache_read: int, cache_write: int) -> float:
    p = _PRICING.get(model, _DEFAULT_PRICING)
    return inp * p["input"] + out * p["output"] + cache_read * p["cache_read"] + cache_write * p["cache_write"]


@dataclass
class UsageRecord:
    call_type: str     # decompose_pdf | teach_turn | intro_turn | tts_prep | generate_instructions
    model: str
    session_id: str | None
    timestamp: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def estimated_cost_usd(self) -> float:
        return _price(self.model, self.input_tokens, self.output_tokens,
                      self.cache_read_tokens, self.cache_write_tokens)


def _agg(records: list[UsageRecord]) -> dict:
    inp = sum(r.input_tokens for r in records)
    out = sum(r.output_tokens for r in records)
    cr  = sum(r.cache_read_tokens for r in records)
    cw  = sum(r.cache_write_tokens for r in records)
    calls = len(records)
    # Cost: use first record's model for aggregates (mixed models → sum individually)
    cost = sum(r.estimated_cost_usd for r in records)
    return {
        "calls": calls,
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_tokens": cr,
        "cache_write_tokens": cw,
        "total_tokens": inp + out,
        "estimated_cost_usd": round(cost, 6),
    }


class TokenUsageTracker:
    """Accumulates token usage records. Thread-safe."""

    def __init__(self) -> None:
        self._records: list[UsageRecord] = []
        self._lock = threading.Lock()

    def record(
        self,
        call_type: str,
        model: str,
        usage,  # anthropic Usage object
        session_id: str | None = None,
    ) -> None:
        rec = UsageRecord(
            call_type=call_type,
            model=model,
            session_id=session_id,
            timestamp=time.time(),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )
        with self._lock:
            self._records.append(rec)

    def summary(self) -> dict:
        with self._lock:
            records = list(self._records)

        by_type: dict[str, list[UsageRecord]] = defaultdict(list)
        by_model: dict[str, list[UsageRecord]] = defaultdict(list)
        for r in records:
            by_type[r.call_type].append(r)
            by_model[r.model].append(r)

        return {
            "totals": _agg(records),
            "by_call_type": {ct: _agg(recs) for ct, recs in sorted(by_type.items())},
            "by_model": {m: _agg(recs) for m, recs in sorted(by_model.items())},
        }

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
