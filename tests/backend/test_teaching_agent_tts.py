"""Unit tests for TeachingAgent hybrid TTS behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from backend.services.tts import TTSSynthesisResult
from shared.lesson import Curriculum
from shared.teaching_agent import TeachingAgent


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


class _FakeStream:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    @property
    def text_stream(self):
        return iter(["first line\n", "second line"])

    def get_final_message(self):
        block = SimpleNamespace(type="text", text="first line\nsecond line")
        usage = SimpleNamespace(
            input_tokens=0,
            output_tokens=0,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        return SimpleNamespace(content=[block], usage=usage)


class _FakeAnthropicClient:
    def __init__(self, *args, **kwargs):
        self.messages = self

    def stream(self, **kwargs):
        return _FakeStream()


def test_openai_failure_falls_back_to_kokoro_for_remaining_chunks():
    primary = _PrimaryFailingProvider()
    fallback = _FallbackProvider()
    audio_chunks: list[np.ndarray] = []
    errors: list[str] = []

    agent = TeachingAgent(
        llm_model="claude-sonnet-4-6",
        tts_provider=primary,
        fallback_tts_provider=fallback,
        tts_voice="alloy",
        on_audio_chunk=lambda audio, _turn, _chunk: audio_chunks.append(audio),
        on_error=lambda message: errors.append(message),
    )
    agent.prepare_for_tts = lambda text: f"prep:{text}"  # type: ignore[method-assign]

    curriculum = Curriculum(
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
    messages = [{"role": "user", "content": "start"}]

    with patch.dict("sys.modules", {"anthropic": SimpleNamespace(Anthropic=_FakeAnthropicClient)}):
        agent._do_single_llm_turn(curriculum, messages, agent_instructions=None, _tools=[])

    # Primary provider fails once, then fallback is used for all chunks in this turn.
    assert primary.calls == 1
    assert fallback.calls == 2
    assert primary.inputs == ["first line"]
    assert fallback.inputs == ["prep:first line", "prep:second line"]
    assert len(audio_chunks) == 2
    assert any("Switching to Kokoro fallback" in msg for msg in errors)


def test_openai_teach_provider_returns_tool_use_and_records_usage():
    tts = _FallbackProvider()
    token_usage_calls: list[tuple[str, str, object]] = []
    audio_chunks: list[np.ndarray] = []

    agent = TeachingAgent(
        llm_model="claude-sonnet-4-6",
        teach_llm_provider="openai",
        teach_llm_model="gpt-4o-mini",
        openai_api_key="test-key",
        tts_provider=tts,
        tts_voice="alloy",
        on_audio_chunk=lambda audio, _turn, _chunk: audio_chunks.append(audio),
        on_token_usage=lambda call_type, model, usage: token_usage_calls.append((call_type, model, usage)),
    )

    def _fake_openai_chat_turn(**kwargs):
        usage = SimpleNamespace(
            input_tokens=12,
            output_tokens=34,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        return (
            [
                {"type": "text", "text": "first line\nsecond line"},
                {"type": "tool_use", "id": "call_1", "name": "advance_to_next_section", "input": {"evidence": "ok"}},
            ],
            "first line\nsecond line",
            usage,
        )

    agent._openai_chat_turn = _fake_openai_chat_turn  # type: ignore[method-assign]
    agent.prepare_for_tts = lambda text: f"prep:{text}"  # type: ignore[method-assign]

    curriculum = Curriculum(
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
    messages = [{"role": "user", "content": "start"}]

    tool = agent._do_single_llm_turn(curriculum, messages, agent_instructions=None)

    assert tool is not None
    assert tool.type == "tool_use"
    assert tool.name == "advance_to_next_section"
    assert len(token_usage_calls) == 1
    assert token_usage_calls[0][0] == "teach_turn"
    assert token_usage_calls[0][1] == "gpt-4o-mini"
    assert tts.calls == 2
    assert tts.inputs == ["prep:first line", "prep:second line"]
    assert len(audio_chunks) == 2
