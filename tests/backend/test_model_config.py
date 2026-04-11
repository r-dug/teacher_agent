"""Tests for the LLM provider chain config (Plan B follow-up A)."""

from __future__ import annotations

import os

import pytest

from backend.services.agents.model_config import (
    ChainSpec,
    ModelSpec,
    _build_one,
    build_chain,
)
from backend.services.agents.providers.anthropic import AnthropicLLMProvider
from backend.services.agents.providers.fallback import FallbackLLMProvider
from backend.services.agents.providers.openai import OpenAILLMProvider


def _openai_spec(name: str = "gpt-4o") -> ModelSpec:
    return ModelSpec(
        name=name,
        source="openai",
        modalities=frozenset({"text"}),
        context_window=128_000,
        source_config={
            "api_key_env": "OPENAI_API_KEY",
            "timeout_s": 30.0,
            "max_retries": 1,
        },
    )


def _anthropic_spec(name: str = "claude-sonnet-4-6") -> ModelSpec:
    return ModelSpec(
        name=name,
        source="anthropic",
        modalities=frozenset({"text"}),
        context_window=200_000,
        source_config={
            "api_key_env": "ANTHROPIC_API_KEY",
            "max_retries": 6,
        },
    )


def test_build_chain_single_provider_returns_bare(monkeypatch):
    """ChainSpec with no fallbacks returns the primary provider directly,
    not wrapped in FallbackLLMProvider."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    spec = ChainSpec(role="teach", primary=_openai_spec())
    chain = build_chain(spec)
    assert isinstance(chain, OpenAILLMProvider)
    assert chain.model == "gpt-4o"


def test_build_chain_with_fallback_returns_fallback_wrapper(monkeypatch):
    """ChainSpec with fallbacks returns a FallbackLLMProvider wrapping
    the primary + fallbacks."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    spec = ChainSpec(
        role="teach",
        primary=_openai_spec(),
        fallbacks=[_anthropic_spec()],
    )
    chain = build_chain(spec)
    assert isinstance(chain, FallbackLLMProvider)
    # Primary first, fallback second
    assert chain.model == "gpt-4o"  # FallbackLLMProvider.model returns primary's
    assert chain.name == "openai/anthropic"


def test_build_chain_skips_misconfigured_fallback(monkeypatch):
    """If a fallback's required env var is missing, the chain logs a
    warning and continues without it (instead of crashing)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    spec = ChainSpec(
        role="teach",
        primary=_openai_spec(),
        fallbacks=[_anthropic_spec()],  # ANTHROPIC_API_KEY missing
    )
    chain = build_chain(spec)
    # Fallback was skipped → bare OpenAI provider, no FallbackLLMProvider wrapper
    assert isinstance(chain, OpenAILLMProvider)


def test_build_chain_primary_missing_env_raises(monkeypatch):
    """If the PRIMARY's env var is missing, build_chain raises so the
    misconfiguration surfaces at startup."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    spec = ChainSpec(role="teach", primary=_openai_spec())
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        build_chain(spec)


def test_build_one_unknown_source_raises():
    """Typo in ModelSpec.source surfaces as a ValueError."""
    spec = ModelSpec(
        name="bogus",
        source="not-a-real-source",  # type: ignore[arg-type]
        modalities=frozenset({"text"}),
        context_window=1000,
    )
    with pytest.raises(ValueError, match="Unknown ModelSpec.source"):
        _build_one(spec)


def test_chain_spec_immutable():
    """ChainSpec is frozen — assignment must raise."""
    spec = ChainSpec(role="teach", primary=_openai_spec())
    with pytest.raises(Exception):  # FrozenInstanceError
        spec.role = "decompose"  # type: ignore[misc]


def test_model_chains_module_imports():
    """The concrete chains in model_chains.py are constructible (no
    typos in field names, no missing imports)."""
    from backend.services.agents import model_chains

    # Spot-check a few chains
    assert model_chains.TEACH_CHAIN.role == "teach"
    assert model_chains.TEACH_CHAIN.primary.source == "openai"
    assert model_chains.DECOMPOSE_CHAIN.role == "decompose"
    assert model_chains.SEARCH_CHAIN.role == "search"
    assert model_chains.EMBEDDING_CHAIN.primary.name == "text-embedding-3-small"
    # ROLE_TO_CHAIN must contain every chain we define
    assert "teach" in model_chains.ROLE_TO_CHAIN
    assert "decompose" in model_chains.ROLE_TO_CHAIN
    assert "search" in model_chains.ROLE_TO_CHAIN
    assert "embed" in model_chains.ROLE_TO_CHAIN
