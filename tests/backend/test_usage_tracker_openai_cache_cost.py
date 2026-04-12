"""Regression test for the OpenAI cached-token double-counting bug.

The old ``_api_cost`` formula (``inp * input_rate + cr * cache_rate``)
assumed the Anthropic convention where ``input_tokens`` excludes
cached reads.  When Cache Plan C1 started landing actual cache hits
on the OpenAI Responses API (where ``input_tokens`` INCLUDES cached
tokens), the formula double-counted: billing the cached portion once
at the full input rate and again at the cache-read rate.

This test passes 1M total input tokens with 800K cached through
``_api_cost`` and asserts the result matches the correct expected
cost (uncached tokens at full rate + cached at cache-read rate),
NOT the old buggy formula.
"""

from __future__ import annotations

import pytest

from backend.usage_tracker import _api_cost


def test_openai_cache_read_not_double_counted():
    """gpt-4o: 1M total input (800K cached), 0 output, 0 cache writes.

    Correct cost under the fix:
        uncached = 1M - 800K = 200K
        200K * $2.50/M + 800K * $1.25/M = $0.50 + $1.00 = $1.50

    Under the old buggy formula:
        1M * $2.50/M + 800K * $1.25/M = $2.50 + $1.00 = $3.50  (wrong)
    """
    cost = _api_cost(
        model="gpt-4o",
        inp=1_000_000,
        out=0,
        cr=800_000,
        cw=0,
    )
    assert cost == pytest.approx(1.50, abs=1e-6)


def test_openai_no_cache_baseline():
    """Same call with zero cache reads — must still match published pricing.

    1M input at $2.50/M + 1M output at $10/M = $12.50.
    """
    cost = _api_cost(
        model="gpt-4o",
        inp=1_000_000,
        out=1_000_000,
        cr=0,
        cw=0,
    )
    assert cost == pytest.approx(12.50, abs=1e-6)


def test_openai_all_tokens_cached():
    """1M input, 100% cache hits: everything billed at cache-read rate.

    1M * $1.25/M = $1.25.  No output to keep the math clean.
    """
    cost = _api_cost(
        model="gpt-4o",
        inp=1_000_000,
        out=0,
        cr=1_000_000,
        cw=0,
    )
    assert cost == pytest.approx(1.25, abs=1e-6)


def test_anthropic_with_cache_read_and_create():
    """Claude Sonnet with a cache-create turn followed by cache-read turns.

    Normalized ``inp`` is TOTAL input (includes both cache_read and
    cache_create).  _api_cost subtracts both before multiplying by
    the regular input rate.

    Example: 1.2M total input = 500K uncached + 500K cache_read + 200K cache_create.
    Cost:
      uncached 500K  * $3.00/M = $1.50
      cache_read 500K * $0.30/M = $0.15
      cache_create 200K * $3.75/M = $0.75
      total = $2.40
    """
    cost = _api_cost(
        model="claude-sonnet-4-6",
        inp=1_200_000,
        out=0,
        cr=500_000,
        cw=200_000,
    )
    assert cost == pytest.approx(2.40, abs=1e-6)


def test_unknown_model_uses_default_pricing():
    """Unknown models fall back to ``_DEFAULT_PRICING`` — cost is nonzero,
    so an unexpected model name doesn't silently report $0."""
    cost = _api_cost(
        model="some-future-model-not-registered",
        inp=1_000_000,
        out=0,
        cr=0,
        cw=0,
    )
    assert cost > 0


def test_uncached_minimum_floor():
    """Pathological case: cached + cache_create > total (shouldn't happen
    but must not produce a negative-rate contribution).  Floor uncached
    at zero."""
    cost = _api_cost(
        model="gpt-4o",
        inp=100,
        out=0,
        cr=80,
        cw=50,  # cr + cw = 130 > inp = 100
    )
    # uncached = max(100 - 80 - 50, 0) = 0
    # cost = 0 * 2.50/M + 80 * 1.25/M + 50 * 0.0/M = $0.0001
    expected = 80 * 1.25 / 1_000_000 + 50 * 0.0 / 1_000_000
    assert cost == pytest.approx(expected, abs=1e-9)
