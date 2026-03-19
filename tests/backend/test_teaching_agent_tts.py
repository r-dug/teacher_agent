"""Unit tests for TeacherAgent + TTSPipeline hybrid TTS behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from backend.services.agents.callbacks import TeachingCallbacks
from backend.services.agents.curriculum import Curriculum
from backend.services.agents.providers.base import LLMProvider, LLMTurnResult
from backend.services.agents.teacher_agent import TeacherAgent
from backend.services.voice.tts import TTSSynthesisResult


# ── Shared fakes ──────────────────────────────────────────────────────────────

class _PrimaryFailingProvider:
    requires_preprocessing = False

    def __init__(self) -> None:
        self.calls = 0
        self.inputs: list[str] = []

    def synthesize(self, text: str, voice: str) -> TTSSynthesisResult:
        self.calls += 1
        self.inputs.append(text)
        raise RuntimeError("openai unavailable")


class _FallbackProvider:
    requires_preprocessing = True

    def __init__(self) -> None:
        self.calls = 0
        self.inputs: list[str] = []

    def synthesize(self, text: str, voice: str) -> TTSSynthesisResult:
        self.calls += 1
        self.inputs.append(text)
        return TTSSynthesisResult(
            audio=np.ones(8, dtype=np.float32),
            sample_rate=24000,
            voice="af_bella",
            characters=len(text),
            synthesis_ms=10,
            estimated_cost_usd=0.0,
        )


class _GoodTTSProvider:
    requires_preprocessing = True

    def __init__(self) -> None:
        self.calls = 0
        self.inputs: list[str] = []

    def synthesize(self, text: str, voice: str) -> TTSSynthesisResult:
        self.calls += 1
        self.inputs.append(text)
        return TTSSynthesisResult(
            audio=np.ones(8, dtype=np.float32),
            sample_rate=24000,
            voice="af_bella",
            characters=len(text),
            synthesis_ms=10,
            estimated_cost_usd=0.0,
        )


class _FakeLLMProvider(LLMProvider):
    """Deterministic LLM provider for testing."""

    def __init__(self, content_text: str, tool_use=None, usage=None):
        self._content_text = content_text
        self._tool_use = tool_use
        self._usage = usage or SimpleNamespace(
            input_tokens=10,
            output_tokens=20,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )

    @property
    def name(self) -> str:
        return "fake"

    def do_turn(self, model, system, messages, tools, on_text_chunk=None):
        # Simulate streaming by calling on_text_chunk per chunk
        if on_text_chunk:
            for chunk in self._content_text.split("\n"):
                on_text_chunk(chunk + "\n")

        content_blocks = [{"type": "text", "text": self._content_text}]
        if self._tool_use is not None:
            content_blocks.append({
                "type": "tool_use",
                "id": self._tool_use.id,
                "name": self._tool_use.name,
                "input": self._tool_use.input,
            })

        return LLMTurnResult(
            content_blocks=content_blocks,
            content_text=self._content_text,
            tool_use=self._tool_use,
            usage=self._usage,
        )


def _make_agent(
    llm_provider=None,
    tts_providers: list | None = None,
    callbacks: TeachingCallbacks | None = None,
    preprocess_fn=None,
) -> TeacherAgent:
    """Construct a minimal TeacherAgent for testing."""
    if llm_provider is None:
        llm_provider = _FakeLLMProvider("hello")

    cb = callbacks or TeachingCallbacks()

    agent = TeacherAgent(
        llm_provider=llm_provider,
        callbacks=cb,
        tts_providers=tts_providers or [],
        model="claude-sonnet-4-6",
    )
    if preprocess_fn is not None:
        agent.prepare_for_tts = preprocess_fn  # type: ignore[method-assign]
    return agent


def _make_curriculum() -> Curriculum:
    return Curriculum(
        title="T",
        sections=[{
            "title": "S",
            "content": "C",
            "key_concepts": ["k1"],
            "page_start": 1,
            "page_end": 1,
        }],
        idx=0,
    )


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_openai_failure_falls_back_to_kokoro_for_remaining_chunks():
    """When primary TTS fails, fallback handles all remaining chunks in that turn."""
    primary = _PrimaryFailingProvider()
    fallback = _FallbackProvider()
    audio_chunks: list[np.ndarray] = []
    errors: list[str] = []

    cb = TeachingCallbacks(
        on_audio_chunk=lambda audio, _turn, _chunk: audio_chunks.append(audio),
        on_error=lambda message: errors.append(message),
    )
    llm_provider = _FakeLLMProvider("first line\nsecond line")
    agent = _make_agent(
        llm_provider=llm_provider,
        tts_providers=[primary, fallback],
        callbacks=cb,
        preprocess_fn=lambda text: f"prep:{text}",
    )

    curriculum = _make_curriculum()
    messages = [{"role": "user", "content": "start"}]

    agent._do_single_llm_turn(curriculum, messages, agent_instructions=None, _tools=[])

    # Primary provider fails on first chunk, then fallback takes over for all chunks.
    assert primary.calls == 1
    assert fallback.calls == 2
    assert primary.inputs == ["first line"]
    assert fallback.inputs == ["prep:first line", "prep:second line"]
    assert len(audio_chunks) == 2
    assert any("Switching to fallback" in msg for msg in errors)


def test_tts_provider_returns_tool_use_and_records_usage():
    """Provider returns tool_use; it is forwarded from _do_single_llm_turn."""
    tts = _GoodTTSProvider()
    token_usage_calls: list[tuple[str, str, object]] = []
    audio_chunks: list[np.ndarray] = []

    usage = SimpleNamespace(
        input_tokens=12,
        output_tokens=34,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    tool_use = SimpleNamespace(
        type="tool_use",
        id="call_1",
        name="advance_to_next_section",
        input={"evidence": "ok"},
    )
    llm_provider = _FakeLLMProvider(
        content_text="first line\nsecond line",
        tool_use=tool_use,
        usage=usage,
    )

    cb = TeachingCallbacks(
        on_audio_chunk=lambda audio, _turn, _chunk: audio_chunks.append(audio),
        on_token_usage=lambda call_type, model, u: token_usage_calls.append((call_type, model, u)),
    )
    agent = _make_agent(
        llm_provider=llm_provider,
        tts_providers=[tts],
        callbacks=cb,
        preprocess_fn=lambda text: f"prep:{text}",
    )

    curriculum = _make_curriculum()
    messages = [{"role": "user", "content": "start"}]

    result = agent._do_single_llm_turn(curriculum, messages, agent_instructions=None)

    assert result is not None
    assert result.type == "tool_use"
    assert result.name == "advance_to_next_section"
    assert len(token_usage_calls) == 1
    assert token_usage_calls[0][0] == "teach_turn"
    assert tts.calls == 2
    assert tts.inputs == ["prep:first line", "prep:second line"]
    assert len(audio_chunks) == 2
