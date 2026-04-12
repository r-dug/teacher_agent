"""Tests for Cache Plan C3: cache-effectiveness telemetry.

Verifies that ``TeacherAgent`` accumulates per-session cache counters
across LLM turns and that ``cache_stats()`` exposes them in the
expected shape.  Also checks the per-turn log line includes the
hit_rate field so operators can tail it during a teaching session.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from backend.services.agents.callbacks import AgentCallbacks
from backend.services.agents.curriculum import Curriculum
from backend.services.agents.providers.base import LLMProvider, LLMTurnResult
from backend.services.agents.teacher_agent import TeacherAgent


def _make_usage(input_tokens: int, cached: int) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=10,
        cache_read_input_tokens=cached,
        cache_creation_input_tokens=0,
    )


class _ScriptedProvider(LLMProvider):
    """Returns a pre-defined sequence of usage numbers so the test can
    verify counter accumulation without needing a real LLM.
    """

    def __init__(self, usages: list[SimpleNamespace]):
        self._usages = list(usages)
        self._idx = 0

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def model(self) -> str:
        return "scripted-model"

    def complete(self, system, messages, max_tokens=1024, cache_key=None):
        return ("ok", self._next_usage())

    def do_turn(self, model, system, messages, tools, on_text_chunk=None, cache_key=None):
        return LLMTurnResult(
            content_blocks=[{"type": "text", "text": "ok"}],
            content_text="ok",
            usage=self._next_usage(),
            tool_uses=[],
        )

    def _next_usage(self) -> SimpleNamespace:
        if self._idx >= len(self._usages):
            return _make_usage(0, 0)
        u = self._usages[self._idx]
        self._idx += 1
        return u


def _make_curriculum() -> Curriculum:
    return Curriculum(
        title="Test",
        sections=[
            {
                "title": "S1",
                "content": "content",
                "key_concepts": ["a"],
                "page_start": 1,
                "page_end": 2,
            }
        ],
    )


def _make_agent(provider: LLMProvider, model: str = "scripted-model") -> TeacherAgent:
    return TeacherAgent(
        llm_provider=provider,
        callbacks=AgentCallbacks(),
        tts_providers=[],
        tts_voice="",
        model=model,
        cache_key="test-session",
    )


def _make_usage_full(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
    cache_write: int = 0,
) -> SimpleNamespace:
    """Full usage shape for tests that need to exercise all four counters."""
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
    )


# ── cache_stats initial state ────────────────────────────────────────────────


def test_cache_stats_zero_before_any_turn():
    p = _ScriptedProvider([])
    agent = _make_agent(p)
    stats = agent.cache_stats()
    assert stats == {
        "turns": 0,
        "tokens_in": 0,
        "tokens_cached": 0,
        "hit_rate": 0.0,
        "dollars_spent": 0.0,
        "dollars_saved": 0.0,
    }


# ── accumulation across turns ────────────────────────────────────────────────


def test_cache_stats_accumulates_across_turns():
    """Two turns: the first is a cache miss (0 cached), the second is a
    hit (800 of 1000 cached).  cache_stats() should report the cumulative
    totals and the combined hit rate."""
    p = _ScriptedProvider([
        _make_usage(input_tokens=1000, cached=0),
        _make_usage(input_tokens=1000, cached=800),
    ])
    agent = _make_agent(p)
    curriculum = _make_curriculum()
    messages: list[dict] = [{"role": "user", "content": "hello"}]

    agent._do_single_llm_turn(
        messages=messages,
        curriculum=curriculum,
        agent_instructions=None,
        lesson_goal=None,
    )
    agent._do_single_llm_turn(
        messages=messages,
        curriculum=curriculum,
        agent_instructions=None,
        lesson_goal=None,
    )

    stats = agent.cache_stats()
    assert stats["turns"] == 2
    assert stats["tokens_in"] == 2000
    assert stats["tokens_cached"] == 800
    # 800 / 2000 = 0.4
    assert stats["hit_rate"] == pytest.approx(0.4, abs=1e-6)


# ── per-turn log line includes hit_rate ──────────────────────────────────────


def test_per_turn_log_line_includes_hit_rate(caplog):
    p = _ScriptedProvider([_make_usage(input_tokens=500, cached=400)])
    agent = _make_agent(p)
    curriculum = _make_curriculum()
    messages: list[dict] = [{"role": "user", "content": "hello"}]

    with caplog.at_level(logging.INFO, logger="backend.services.agents.teacher_agent"):
        agent._do_single_llm_turn(
            messages=messages,
            curriculum=curriculum,
            agent_instructions=None,
            lesson_goal=None,
        )

    llm_lines = [r for r in caplog.records if "[llm]" in r.getMessage()]
    assert llm_lines, "expected a per-turn [llm] log line"
    msg = llm_lines[-1].getMessage()
    assert "hit_rate=" in msg
    # 400 / 500 = 0.80
    assert "hit_rate=0.80" in msg
    assert "cached=400" in msg
    assert "tokens_in=500" in msg


# ── no division-by-zero on a zero-input turn ─────────────────────────────────


def test_hit_rate_zero_when_no_input_tokens(caplog):
    """Edge case: a provider may report zero input_tokens on error paths."""
    p = _ScriptedProvider([_make_usage(input_tokens=0, cached=0)])
    agent = _make_agent(p)
    curriculum = _make_curriculum()
    messages: list[dict] = [{"role": "user", "content": "hello"}]

    with caplog.at_level(logging.INFO, logger="backend.services.agents.teacher_agent"):
        agent._do_single_llm_turn(
            messages=messages,
            curriculum=curriculum,
            agent_instructions=None,
            lesson_goal=None,
        )

    stats = agent.cache_stats()
    assert stats["hit_rate"] == 0.0
    # Log line still emitted, no ZeroDivisionError.
    llm_lines = [r for r in caplog.records if "[llm]" in r.getMessage()]
    assert llm_lines
    assert "hit_rate=0.00" in llm_lines[-1].getMessage()


# ── dollars_spent / dollars_saved (pricing consolidation) ────────────────────


def test_cache_stats_dollars_zero_for_unknown_model():
    """Unknown model names don't have pricing, so both dollar fields are 0.0.
    Token counts are still reported accurately."""
    p = _ScriptedProvider([
        _make_usage_full(input_tokens=1000, output_tokens=100, cache_read=400),
    ])
    agent = _make_agent(p, model="scripted-model")  # not registered
    curriculum = _make_curriculum()
    messages: list[dict] = [{"role": "user", "content": "hi"}]
    agent._do_single_llm_turn(
        messages=messages,
        curriculum=curriculum,
        agent_instructions=None,
        lesson_goal=None,
    )
    stats = agent.cache_stats()
    assert stats["dollars_spent"] == 0.0
    assert stats["dollars_saved"] == 0.0
    assert stats["tokens_in"] == 1000
    assert stats["tokens_cached"] == 400


def test_cache_stats_dollars_for_gpt_4o():
    """With a real registered model, dollar fields are populated.

    gpt-4o pricing: input $2.50/M, output $10.00/M, cache_read $1.25/M.
    One turn: 1M total input, 800K cached, 50K output.
      uncached = 1M - 800K = 200K
      spent = 200K*$2.50/M + 800K*$1.25/M + 50K*$10.00/M
            = $0.50 + $1.00 + $0.50 = $2.00
      saved = 800K * ($2.50 - $1.25) / M
            = 800K * $1.25 / M = $1.00
    """
    p = _ScriptedProvider([
        _make_usage_full(
            input_tokens=1_000_000,
            output_tokens=50_000,
            cache_read=800_000,
        ),
    ])
    agent = _make_agent(p, model="gpt-4o")
    curriculum = _make_curriculum()
    messages: list[dict] = [{"role": "user", "content": "hi"}]
    agent._do_single_llm_turn(
        messages=messages,
        curriculum=curriculum,
        agent_instructions=None,
        lesson_goal=None,
    )
    stats = agent.cache_stats()
    assert stats["dollars_spent"] == pytest.approx(2.00, abs=1e-6)
    assert stats["dollars_saved"] == pytest.approx(1.00, abs=1e-6)


def test_cache_stats_dollars_saved_zero_on_first_turn_no_cache():
    """First turn in a section has no cache hits, so dollars_saved = 0."""
    p = _ScriptedProvider([
        _make_usage_full(
            input_tokens=500_000,
            output_tokens=10_000,
            cache_read=0,
        ),
    ])
    agent = _make_agent(p, model="gpt-4o")
    curriculum = _make_curriculum()
    messages: list[dict] = [{"role": "user", "content": "hi"}]
    agent._do_single_llm_turn(
        messages=messages,
        curriculum=curriculum,
        agent_instructions=None,
        lesson_goal=None,
    )
    stats = agent.cache_stats()
    assert stats["dollars_saved"] == 0.0
    # spent = 500K*$2.50/M + 10K*$10/M = $1.25 + $0.10 = $1.35
    assert stats["dollars_spent"] == pytest.approx(1.35, abs=1e-6)


def test_cache_stats_includes_cache_write_in_spent():
    """Anthropic-style cache_write (cache_creation_input_tokens) is billed
    separately.  Claude Sonnet: cache_write $3.75/M vs input $3.00/M —
    a premium for the first write, amortized by later reads."""
    p = _ScriptedProvider([
        _make_usage_full(
            input_tokens=1_000_000,
            output_tokens=0,
            cache_read=0,
            cache_write=500_000,
        ),
    ])
    agent = _make_agent(p, model="claude-sonnet-4-6")
    curriculum = _make_curriculum()
    messages: list[dict] = [{"role": "user", "content": "hi"}]
    agent._do_single_llm_turn(
        messages=messages,
        curriculum=curriculum,
        agent_instructions=None,
        lesson_goal=None,
    )
    stats = agent.cache_stats()
    # uncached = 1M - 0 - 500K = 500K
    # spent = 500K*$3 + 500K*$3.75 (all per M) = $1.50 + $1.875 = $3.375
    assert stats["dollars_spent"] == pytest.approx(3.375, abs=1e-6)
    # saved = 500K * ($3 - $3.75) / M = -$0.375 (negative — first write is a premium)
    assert stats["dollars_saved"] == pytest.approx(-0.375, abs=1e-6)
