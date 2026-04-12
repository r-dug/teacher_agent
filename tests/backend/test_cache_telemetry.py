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


def _make_agent(provider: LLMProvider) -> TeacherAgent:
    return TeacherAgent(
        llm_provider=provider,
        callbacks=AgentCallbacks(),
        tts_providers=[],
        tts_voice="",
        model="scripted-model",
        cache_key="test-session",
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
