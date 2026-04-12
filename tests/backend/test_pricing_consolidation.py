"""Tests for the pricing consolidation into ``ModelSpec``.

Pricing used to live as a hardcoded ``_PRICING`` dict in
``backend/usage_tracker.py`` plus a duplicate in the now-deleted
``backend/token_tracker.py``.  It now lives as fields on every
``ModelSpec`` in ``backend/services/agents/model_chains.py``, and
``lookup_pricing(model_name)`` is the single lookup path.

These tests verify the lookup walks the registered chains correctly,
handles version-suffixed model names, respects dash-delimited prefix
boundaries (so ``gpt-4o`` doesn't match ``gpt-4``), and returns
``None`` for truly unknown models.
"""

from __future__ import annotations

import pytest

from backend.services.agents.model_chains import (
    CLAUDE_HAIKU_4_5,
    CLAUDE_SONNET_4_6,
    GPT_4O,
    GPT_4O_MINI,
)
from backend.services.agents.model_config import (
    _DEFAULT_PRICING,
    ModelPricing,
    ModelSpec,
    _dash_prefix_matches,
    lookup_pricing,
)


# ── _dash_prefix_matches ─────────────────────────────────────────────────────


class TestDashPrefixMatches:
    def test_equal_strings_match(self):
        assert _dash_prefix_matches("gpt-4o", "gpt-4o") is True

    def test_dash_delimited_prefix_matches(self):
        assert _dash_prefix_matches("claude-haiku-4-5-20251001", "claude-haiku-4-5") is True

    def test_non_dash_boundary_rejected(self):
        """``gpt-4o`` does NOT start with ``gpt-4`` for pricing purposes."""
        assert _dash_prefix_matches("gpt-4o", "gpt-4") is False

    def test_non_prefix_rejected(self):
        assert _dash_prefix_matches("gpt-4o", "claude-sonnet") is False

    def test_empty_shorter_matches_trivially(self):
        # Edge case — shouldn't matter in practice but be well-defined.
        assert _dash_prefix_matches("gpt-4o", "") is False  # next char is 'g', not '-'


# ── lookup_pricing ───────────────────────────────────────────────────────────


class TestLookupPricing:
    def test_exact_match_gpt_4o(self):
        pricing = lookup_pricing("gpt-4o")
        assert pricing is not None
        assert pricing.input_per_mtok == GPT_4O.input_per_mtok == 2.50
        assert pricing.output_per_mtok == GPT_4O.output_per_mtok == 10.00
        assert pricing.cache_read_per_mtok == 1.25

    def test_exact_match_gpt_4o_mini(self):
        pricing = lookup_pricing("gpt-4o-mini")
        assert pricing is not None
        assert pricing.input_per_mtok == GPT_4O_MINI.input_per_mtok == 0.15
        assert pricing.output_per_mtok == GPT_4O_MINI.output_per_mtok == 0.60

    def test_exact_match_claude_sonnet(self):
        pricing = lookup_pricing("claude-sonnet-4-6")
        assert pricing is not None
        assert pricing.input_per_mtok == CLAUDE_SONNET_4_6.input_per_mtok == 3.00
        assert pricing.cache_write_per_mtok == 3.75

    def test_exact_match_claude_haiku_versioned(self):
        pricing = lookup_pricing("claude-haiku-4-5-20251001")
        assert pricing is not None
        assert pricing.input_per_mtok == CLAUDE_HAIKU_4_5.input_per_mtok == 0.25

    def test_version_suffix_strips_to_registered_base(self):
        """A ``claude-sonnet-4-6-20260101`` should match ``claude-sonnet-4-6``
        via the dash-delimited prefix rule."""
        pricing = lookup_pricing("claude-sonnet-4-6-20260101")
        assert pricing is not None
        assert pricing.input_per_mtok == 3.00

    def test_short_name_matches_registered_long_name(self):
        """The reverse direction: asking for ``claude-haiku-4-5`` should
        match the registered longer name ``claude-haiku-4-5-20251001``."""
        pricing = lookup_pricing("claude-haiku-4-5")
        assert pricing is not None
        assert pricing.input_per_mtok == 0.25

    def test_unknown_model_returns_none(self):
        assert lookup_pricing("some-future-model-not-yet-registered") is None

    def test_gpt_4_does_not_accidentally_match_gpt_4o(self):
        """Prefix matching must respect dash boundaries."""
        # Neither gpt-4 nor gpt-5 is registered; gpt-4o IS registered.
        # Looking up "gpt-4" must NOT silently match gpt-4o pricing.
        assert lookup_pricing("gpt-4") is None

    def test_longest_match_wins(self):
        """When both ``gpt-4o`` and ``gpt-4o-mini`` are registered, an
        exact match for ``gpt-4o-mini`` must return mini pricing, not
        4o pricing."""
        pricing = lookup_pricing("gpt-4o-mini")
        assert pricing is not None
        assert pricing.input_per_mtok == 0.15  # mini, not 2.50

    def test_embedding_model_lookup(self):
        """text-embedding-3-small is registered with zero pricing — it's
        a chat-model-shaped ChainSpec entry that isn't used for chat
        pricing.  Lookup should return the zero pricing (not None)."""
        pricing = lookup_pricing("text-embedding-3-small")
        assert pricing is not None
        assert pricing.input_per_mtok == 0.0
        assert pricing.output_per_mtok == 0.0


# ── ModelPricing.cost arithmetic ─────────────────────────────────────────────


class TestModelPricingCost:
    def test_gpt_4o_cost_arithmetic(self):
        """1M uncached input + 1M output on gpt-4o: $2.50 + $10.00 = $12.50."""
        pricing = lookup_pricing("gpt-4o")
        cost = pricing.cost(
            input_uncached=1_000_000,
            output_tokens=1_000_000,
            cache_read=0,
            cache_write=0,
        )
        assert cost == pytest.approx(12.50, abs=1e-6)

    def test_cache_read_discount(self):
        """1M cache_read tokens on gpt-4o cost $1.25 (vs $2.50 for uncached)."""
        pricing = lookup_pricing("gpt-4o")
        cost = pricing.cost(
            input_uncached=0,
            output_tokens=0,
            cache_read=1_000_000,
            cache_write=0,
        )
        assert cost == pytest.approx(1.25, abs=1e-6)

    def test_zero_tokens_zero_cost(self):
        pricing = lookup_pricing("gpt-4o-mini")
        assert pricing.cost(0, 0, 0, 0) == 0.0

    def test_anthropic_cache_write_premium(self):
        """Claude Sonnet cache_write is MORE expensive than regular input
        ($3.75 vs $3.00 per Mtok).  The first write pays a premium for
        the cached copy; reads amortize it."""
        pricing = lookup_pricing("claude-sonnet-4-6")
        assert pricing.cache_write_per_mtok > pricing.input_per_mtok

    def test_default_pricing_shape(self):
        """``_DEFAULT_PRICING`` is a ModelPricing with nonzero rates so
        unknown models don't silently report zero cost."""
        assert isinstance(_DEFAULT_PRICING, ModelPricing)
        assert _DEFAULT_PRICING.input_per_mtok > 0
        assert _DEFAULT_PRICING.output_per_mtok > 0


# ── ModelSpec.pricing() accessor ─────────────────────────────────────────────


class TestModelSpecPricingAccessor:
    def test_pricing_accessor_returns_ModelPricing(self):
        p = GPT_4O.pricing()
        assert isinstance(p, ModelPricing)
        assert p.input_per_mtok == 2.50

    def test_minimal_spec_defaults_to_zero_pricing(self):
        """A ModelSpec without pricing fields gets zero pricing."""
        spec = ModelSpec(
            name="test",
            source="openai",
            modalities=frozenset({"text"}),
            context_window=1024,
        )
        p = spec.pricing()
        assert p.input_per_mtok == 0.0
        assert p.output_per_mtok == 0.0


# ── dead module guard ────────────────────────────────────────────────────────


def test_token_tracker_module_deleted():
    """backend/token_tracker.py was dead code (zero imports); its pricing
    table duplicated usage_tracker's.  Verify the deletion took effect
    so nothing can accidentally re-import the stale table."""
    with pytest.raises(ModuleNotFoundError):
        import backend.token_tracker  # noqa: F401
