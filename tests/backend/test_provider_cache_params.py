"""Tests for Cache Plan C2: provider-level cache_key / cache_retention plumbing.

These tests mock the underlying SDK clients at the
``responses.create`` / ``messages.stream`` level and assert that the
cache parameters are translated correctly.  No real network calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.services.agents.model_config import (
    ChainSpec,
    ModelSpec,
    _build_one,
    build_chain,
)
from backend.services.agents.providers.anthropic import AnthropicLLMProvider
from backend.services.agents.providers.base import LLMProvider, LLMTurnResult
from backend.services.agents.providers.fallback import FallbackLLMProvider
from backend.services.agents.providers.openai import OpenAILLMProvider


# ── helpers ──────────────────────────────────────────────────────────────────


def _fake_openai_response(text: str = "ok"):
    """Build a fake Responses API response object shaped like the SDK's."""
    usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
    )
    return SimpleNamespace(output_text=text, usage=usage, output=[])


def _make_openai_provider(cache_retention: str | None = None) -> OpenAILLMProvider:
    p = OpenAILLMProvider(
        model="gpt-4o",
        api_key="sk-test",
        cache_retention=cache_retention,
    )
    # Replace the real client with a MagicMock so we can inspect calls.
    p._client = MagicMock()
    return p


# ── OpenAI complete() ────────────────────────────────────────────────────────


class TestOpenAICompleteCacheParams:
    def test_passes_prompt_cache_key(self):
        p = _make_openai_provider()
        p._client.responses.create.return_value = _fake_openai_response()
        p.complete(system="sys", messages=[{"role": "user", "content": "hi"}], cache_key="abc")
        kwargs = p._client.responses.create.call_args.kwargs
        assert kwargs["prompt_cache_key"] == "abc"

    def test_no_prompt_cache_key_when_none(self):
        p = _make_openai_provider()
        p._client.responses.create.return_value = _fake_openai_response()
        p.complete(system="sys", messages=[{"role": "user", "content": "hi"}])
        kwargs = p._client.responses.create.call_args.kwargs
        assert "prompt_cache_key" not in kwargs

    def test_passes_prompt_cache_retention_when_constructed(self):
        p = _make_openai_provider(cache_retention="24h")
        p._client.responses.create.return_value = _fake_openai_response()
        p.complete(system="sys", messages=[{"role": "user", "content": "hi"}])
        kwargs = p._client.responses.create.call_args.kwargs
        assert kwargs["prompt_cache_retention"] == "24h"

    def test_no_prompt_cache_retention_when_not_configured(self):
        p = _make_openai_provider()
        p._client.responses.create.return_value = _fake_openai_response()
        p.complete(system="sys", messages=[{"role": "user", "content": "hi"}])
        kwargs = p._client.responses.create.call_args.kwargs
        assert "prompt_cache_retention" not in kwargs


# ── OpenAI do_turn() ─────────────────────────────────────────────────────────


def _fake_streaming_context():
    """A minimal fake context manager for responses.create(stream=True).

    Produces a single response.completed event with empty output and
    deterministic usage.
    """
    usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
    )
    completed = SimpleNamespace(
        type="response.completed",
        response=SimpleNamespace(usage=usage),
    )
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=iter([completed]))
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


class TestOpenAIDoTurnCacheParams:
    def test_passes_prompt_cache_key(self):
        p = _make_openai_provider()
        p._client.responses.create.return_value = _fake_streaming_context()
        p.do_turn(
            model="gpt-4o",
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            cache_key="enrollment-42",
        )
        kwargs = p._client.responses.create.call_args.kwargs
        assert kwargs["prompt_cache_key"] == "enrollment-42"

    def test_passes_both_key_and_retention(self):
        p = _make_openai_provider(cache_retention="24h")
        p._client.responses.create.return_value = _fake_streaming_context()
        p.do_turn(
            model="gpt-4o",
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            cache_key="enrollment-42",
        )
        kwargs = p._client.responses.create.call_args.kwargs
        assert kwargs["prompt_cache_key"] == "enrollment-42"
        assert kwargs["prompt_cache_retention"] == "24h"

    def test_omits_cache_params_when_unset(self):
        p = _make_openai_provider()
        p._client.responses.create.return_value = _fake_streaming_context()
        p.do_turn(
            model="gpt-4o",
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )
        kwargs = p._client.responses.create.call_args.kwargs
        assert "prompt_cache_key" not in kwargs
        assert "prompt_cache_retention" not in kwargs


# ── Anthropic: accepts cache_key but ignores it ──────────────────────────────


class TestAnthropicIgnoresCacheKey:
    def test_complete_accepts_cache_key_kwarg(self, monkeypatch):
        """Anthropic's complete() must accept cache_key without erroring."""
        p = AnthropicLLMProvider(
            model="claude-sonnet-4-6", api_key="sk-test", max_retries=1
        )
        # Replace the real SDK client with a mock.
        fake_response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="hello")],
            usage=SimpleNamespace(
                input_tokens=5,
                output_tokens=2,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )
        p._client = MagicMock()
        p._client.messages.create.return_value = fake_response

        text, _usage = p.complete(
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            cache_key="should-be-ignored",
        )
        assert text == "hello"
        # cache_key must NOT appear in the Anthropic SDK call (it has
        # no equivalent in the stable Messages API).
        call_kwargs = p._client.messages.create.call_args.kwargs
        assert "cache_key" not in call_kwargs
        assert "prompt_cache_key" not in call_kwargs

    def test_do_turn_accepts_cache_key_kwarg(self):
        """Anthropic's do_turn() must accept cache_key without erroring.

        We mock the streaming context so the provider doesn't hit the API.
        """
        p = AnthropicLLMProvider(
            model="claude-sonnet-4-6", api_key="sk-test", max_retries=1
        )
        # Mock: __enter__ returns a stream-like object with a text_stream
        # iterator and get_final_message() -> final response.
        stream_obj = MagicMock()
        stream_obj.text_stream = iter([])
        stream_obj.get_final_message.return_value = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=SimpleNamespace(
                input_tokens=5,
                output_tokens=2,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=stream_obj)
        ctx.__exit__ = MagicMock(return_value=False)
        p._client = MagicMock()
        p._client.messages.stream.return_value = ctx

        result = p.do_turn(
            model="claude-sonnet-4-6",
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            cache_key="should-be-ignored",
        )
        assert isinstance(result, LLMTurnResult)
        call_kwargs = p._client.messages.stream.call_args.kwargs
        assert "cache_key" not in call_kwargs
        assert "prompt_cache_key" not in call_kwargs


# ── FallbackLLMProvider ──────────────────────────────────────────────────────


class _RecordingProvider(LLMProvider):
    """Records the kwargs passed to do_turn / complete."""

    def __init__(self, name: str = "rec"):
        self._name = name
        self.calls: list[dict] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return "rec-model"

    def do_turn(self, model, system, messages, tools, on_text_chunk=None, cache_key=None):
        self.calls.append(
            {"fn": "do_turn", "cache_key": cache_key, "model": model}
        )
        usage = SimpleNamespace(
            input_tokens=1,
            output_tokens=1,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        return LLMTurnResult(content_blocks=[], content_text="", usage=usage, tool_uses=[])

    def complete(self, system, messages, max_tokens=1024, cache_key=None):
        self.calls.append(
            {"fn": "complete", "cache_key": cache_key, "max_tokens": max_tokens}
        )
        return ("", SimpleNamespace(input_tokens=1, output_tokens=1))


class TestFallbackPropagatesCacheKey:
    def test_do_turn_propagates_cache_key_to_primary(self):
        primary = _RecordingProvider("primary")
        fallback = _RecordingProvider("fallback")
        chain = FallbackLLMProvider([(primary, "gpt-4o"), (fallback, "claude-sonnet-4-6")])
        chain.do_turn(
            model="", system="sys", messages=[], tools=[], cache_key="k",
        )
        assert primary.calls[-1]["cache_key"] == "k"
        assert fallback.calls == []  # primary succeeded, fallback not reached

    def test_complete_propagates_cache_key(self):
        primary = _RecordingProvider("primary")
        chain = FallbackLLMProvider([(primary, "gpt-4o")])
        chain.complete(system="sys", messages=[], cache_key="k")
        assert primary.calls[-1]["cache_key"] == "k"

    def test_cache_key_reaches_fallback_when_primary_raises(self):
        primary = _RecordingProvider("primary")
        fallback = _RecordingProvider("fallback")

        def _boom(*args, **kwargs):
            raise RuntimeError("primary down")

        primary.do_turn = _boom  # type: ignore[assignment]
        chain = FallbackLLMProvider([(primary, "gpt-4o"), (fallback, "claude-sonnet-4-6")])
        chain.do_turn(
            model="", system="sys", messages=[], tools=[], cache_key="k",
        )
        assert fallback.calls[-1]["cache_key"] == "k"


# ── build_chain reads source_config["cache_retention"] ───────────────────────


class TestBuildChainCacheRetention:
    def test_reads_cache_retention_from_source_config(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        spec = ModelSpec(
            name="gpt-4o",
            source="openai",
            modalities=frozenset({"text"}),
            context_window=128_000,
            supports_prefix_cache=True,
            source_config={
                "api_key_env": "OPENAI_API_KEY",
                "cache_retention": "24h",
            },
        )
        provider = _build_one(spec)
        assert isinstance(provider, OpenAILLMProvider)
        assert provider._cache_retention == "24h"

    def test_skips_cache_retention_when_supports_prefix_cache_false(self, monkeypatch):
        """Defensive: if the spec says no prefix-cache support, skip the
        retention param so the backend doesn't reject it on older models."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        spec = ModelSpec(
            name="text-embedding-3-small",  # name doesn't matter here
            source="openai",
            modalities=frozenset({"text"}),
            context_window=128_000,
            supports_prefix_cache=False,
            source_config={
                "api_key_env": "OPENAI_API_KEY",
                "cache_retention": "24h",  # present but should be ignored
            },
        )
        provider = _build_one(spec)
        assert isinstance(provider, OpenAILLMProvider)
        assert provider._cache_retention is None

    def test_cache_retention_absent_by_default(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        spec = ModelSpec(
            name="gpt-4o",
            source="openai",
            modalities=frozenset({"text"}),
            context_window=128_000,
            supports_prefix_cache=True,
            source_config={"api_key_env": "OPENAI_API_KEY"},
        )
        provider = _build_one(spec)
        assert provider._cache_retention is None


# ── model_chains.py TEACH_CHAIN has 24h retention ────────────────────────────


class TestTeachChainConfig:
    def test_gpt_4o_model_spec_has_24h_retention(self):
        from backend.services.agents.model_chains import GPT_4O

        assert GPT_4O.source_config.get("cache_retention") == "24h"

    def test_teach_chain_primary_is_gpt_4o_with_retention(self):
        from backend.services.agents.model_chains import TEACH_CHAIN

        primary = TEACH_CHAIN.primary
        assert primary.source == "openai"
        assert primary.supports_prefix_cache is True
        assert primary.source_config.get("cache_retention") == "24h"
