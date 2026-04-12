"""Tests for the Anthropic provider's usage-normalization helper.

Pricing consolidation fixed a convention mismatch between the two
LLM providers: OpenAI reports ``input_tokens`` as the TOTAL input
(including cached reads), while Anthropic reports it as the UNCACHED
portion only.  To give downstream code (teacher_agent telemetry,
usage_tracker) a single shape, the Anthropic provider now wraps the
raw SDK ``usage`` in a ``SimpleNamespace`` where ``input_tokens`` is
the total.

These tests call the helper directly with a fake Anthropic Usage
object so no SDK mocking is required.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services.agents.providers.anthropic import _normalize_anthropic_usage


def _raw(inp, out, cr, cw):
    return SimpleNamespace(
        input_tokens=inp,
        output_tokens=out,
        cache_read_input_tokens=cr,
        cache_creation_input_tokens=cw,
    )


def test_no_cache_tokens_unchanged():
    """With no cache activity, input_tokens passes through as-is."""
    out = _normalize_anthropic_usage(_raw(100, 50, 0, 0))
    assert out.input_tokens == 100
    assert out.output_tokens == 50
    assert out.cache_read_input_tokens == 0
    assert out.cache_creation_input_tokens == 0


def test_cache_read_added_to_input_total():
    """Anthropic raw: 100 uncached + 95 cache-read = 195 TOTAL."""
    out = _normalize_anthropic_usage(_raw(100, 50, 95, 0))
    assert out.input_tokens == 195
    assert out.cache_read_input_tokens == 95


def test_cache_create_added_to_input_total():
    """Anthropic raw: 100 uncached + 55 cache-create = 155 TOTAL."""
    out = _normalize_anthropic_usage(_raw(100, 50, 0, 55))
    assert out.input_tokens == 155
    assert out.cache_creation_input_tokens == 55


def test_both_cache_buckets_added():
    """Anthropic raw: 26 uncached + 95 read + 55 create = 176 TOTAL.
    Output tokens are copied through unchanged."""
    out = _normalize_anthropic_usage(_raw(26, 32, 95, 55))
    assert out.input_tokens == 26 + 95 + 55 == 176
    assert out.output_tokens == 32
    assert out.cache_read_input_tokens == 95
    assert out.cache_creation_input_tokens == 55


def test_missing_cache_attrs_default_to_zero():
    """Some older Anthropic SDK shapes may not populate the cache fields
    at all.  Must not raise AttributeError."""
    raw = SimpleNamespace(input_tokens=100, output_tokens=50)
    out = _normalize_anthropic_usage(raw)
    assert out.input_tokens == 100
    assert out.cache_read_input_tokens == 0
    assert out.cache_creation_input_tokens == 0


def test_cost_formula_is_correct_after_normalization():
    """Integration check: normalize an Anthropic usage, then push the
    normalized values through ``_api_cost`` and verify the result
    matches the published rates."""
    from backend.usage_tracker import _api_cost

    normalized = _normalize_anthropic_usage(_raw(100, 50, 95, 55))
    # Sonnet rates: input $3, output $15, cache_read $0.30, cache_write $3.75 (all per Mtok)
    # Expected cost:
    #   uncached 100  * $3 / M    = 0.0003
    #   output    50  * $15 / M   = 0.00075
    #   read      95  * $0.30 / M = 0.0000285
    #   create    55  * $3.75 / M = 0.00020625
    expected = (100 * 3 + 50 * 15 + 95 * 0.30 + 55 * 3.75) / 1_000_000
    cost = _api_cost(
        model="claude-sonnet-4-6",
        inp=normalized.input_tokens,
        out=normalized.output_tokens,
        cr=normalized.cache_read_input_tokens,
        cw=normalized.cache_creation_input_tokens,
    )
    assert cost == pytest.approx(expected, abs=1e-9)
