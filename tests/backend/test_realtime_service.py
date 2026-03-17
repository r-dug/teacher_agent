"""Unit tests for realtime voice helpers."""

from __future__ import annotations

import base64
import json

import numpy as np
import pytest

from backend.services.realtime import (
    float32_b64_to_pcm16_b64,
    run_realtime_voice_turn,
    pcm16_b64_to_float32,
    usage_from_realtime,
)


def test_pcm16_round_trip_close():
    audio = np.array([0.0, 0.25, -0.5, 1.0, -1.0], dtype=np.float32)
    b64_f32 = base64.b64encode(audio.tobytes()).decode()
    b64_pcm16 = float32_b64_to_pcm16_b64(
        b64_f32,
        sample_rate=24000,
        target_sample_rate=24000,
    )
    out = pcm16_b64_to_float32(b64_pcm16)
    assert out.shape == audio.shape
    assert np.allclose(out, audio, atol=2e-4)


def test_resample_changes_length_when_sample_rate_differs():
    audio = np.zeros(16000, dtype=np.float32)  # 1 second @ 16k
    b64_f32 = base64.b64encode(audio.tobytes()).decode()
    b64_pcm16 = float32_b64_to_pcm16_b64(
        b64_f32,
        sample_rate=16000,
        target_sample_rate=24000,
    )
    raw = base64.b64decode(b64_pcm16)
    # pcm16: 2 bytes/sample => 24000 samples expected for 1 sec.
    assert len(raw) == 24000 * 2


def test_usage_from_realtime_maps_fields():
    usage = {
        "input_tokens": 123,
        "output_tokens": 45,
        "input_token_details": {"cached_tokens": 6},
    }
    u = usage_from_realtime(usage)
    assert u.input_tokens == 123
    assert u.output_tokens == 45
    assert u.cache_read_input_tokens == 6
    assert u.cache_creation_input_tokens == 0


@pytest.mark.asyncio
async def test_run_realtime_voice_turn_accepts_audio_transcript_deltas(monkeypatch):
    class _FakeWS:
        def __init__(self, events: list[dict]) -> None:
            self._events = [json.dumps(e) for e in events]
            self.sent: list[str] = []

        async def send(self, payload: str) -> None:
            self.sent.append(payload)

        async def recv(self) -> str:
            return self._events.pop(0)

    class _FakeCtx:
        def __init__(self, ws: _FakeWS) -> None:
            self._ws = ws

        async def __aenter__(self) -> _FakeWS:
            return self._ws

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    events = [
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "hello"},
        {"type": "response.audio_transcript.delta", "delta": "Hi"},
        {"type": "response.audio_transcript.delta", "delta": " there"},
        {"type": "response.done", "response": {"usage": {"input_tokens": 1, "output_tokens": 2}}},
    ]
    ws = _FakeWS(events)

    def _fake_connect(*args, **kwargs):
        return _FakeCtx(ws)

    monkeypatch.setattr("backend.services.realtime.connect", _fake_connect)

    audio = np.zeros(1600, dtype=np.float32)
    audio_b64 = base64.b64encode(audio.tobytes()).decode()

    deltas: list[str] = []
    summary = await run_realtime_voice_turn(
        audio_b64=audio_b64,
        sample_rate=16000,
        api_key="test-key",
        model="gpt-realtime-mini",
        voice="alloy",
        timeout_seconds=1.0,
        max_retries=0,
        on_text_delta=lambda d: _capture_delta(d, deltas),
    )

    assert summary.user_transcript == "hello"
    assert summary.assistant_text == "Hi there"
    assert deltas == ["Hi", " there"]


async def _capture_delta(delta: str, sink: list[str]) -> None:
    sink.append(delta)
