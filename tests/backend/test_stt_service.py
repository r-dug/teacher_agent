"""Tests for the STT service (mocked model)."""

from __future__ import annotations

import base64
import pytest
from unittest.mock import MagicMock

from backend.services.voice.stt import (
    _transcribe_sync,
    select_stt_provider,
    transcribe_file_openai,
    transcribe_openai,
)


def _make_audio_b64(n_samples: int = 1600, sample_rate: int = 16000) -> str:
    """Generate a base64-encoded silent float32 PCM audio blob."""
    import numpy as np
    audio = np.zeros(n_samples, dtype=np.float32)
    return base64.b64encode(audio.tobytes()).decode()


def test_transcribe_calls_model_sync(tmp_path, monkeypatch):
    """_transcribe_sync() should write a temp WAV and call model.transcribe()."""
    mock_model = MagicMock()
    mock_model.transcribe.return_value = "hello world"

    audio_b64 = _make_audio_b64()
    audio_bytes = base64.b64decode(audio_b64)
    result = _transcribe_sync(audio_bytes, sample_rate=16000, model=mock_model, language=None)

    assert result == "hello world"
    mock_model.transcribe.assert_called_once()
    # The temp file path argument should end in .wav
    call_args = mock_model.transcribe.call_args
    assert call_args[0][0].endswith(".wav")


def test_transcribe_passes_language_sync(tmp_path):
    mock_model = MagicMock()
    mock_model.transcribe.return_value = "bonjour"

    audio_b64 = _make_audio_b64()
    audio_bytes = base64.b64decode(audio_b64)
    result = _transcribe_sync(audio_bytes, sample_rate=16000, model=mock_model, language="fr")

    assert result == "bonjour"
    _, kwargs = mock_model.transcribe.call_args
    assert kwargs.get("language") == "fr"


def test_transcribe_cleans_up_temp_file_sync(tmp_path, monkeypatch):
    """The temp WAV file should be deleted after _transcribe_sync()."""
    import os
    created_paths: list[str] = []
    original_write = None

    import soundfile as sf
    original_write = sf.write

    def tracking_write(path, *args, **kwargs):
        created_paths.append(path)
        return original_write(path, *args, **kwargs)

    monkeypatch.setattr("backend.services.voice.stt.sf.write", tracking_write)

    mock_model = MagicMock()
    mock_model.transcribe.return_value = "test"

    audio_bytes = base64.b64decode(_make_audio_b64())
    _transcribe_sync(audio_bytes, 16000, mock_model, language=None)

    for path in created_paths:
        assert not os.path.exists(path), f"Temp file was not cleaned up: {path}"


def test_select_stt_provider_defaults_to_local():
    assert select_stt_provider(None) == "local"
    assert select_stt_provider("openai") == "openai"
    assert select_stt_provider("LOCAL") == "local"
    assert select_stt_provider("invalid") == "local"


@pytest.mark.asyncio
async def test_transcribe_openai_requires_api_key():
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        await transcribe_openai(
            _make_audio_b64(),
            sample_rate=16000,
            api_key=None,
        )


@pytest.mark.asyncio
async def test_transcribe_openai_posts_audio(monkeypatch):
    captured: dict = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"text": "hello from openai"}

    class _Client:
        def __init__(self, timeout: float):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, headers: dict, data: dict, files: dict):
            captured["url"] = url
            captured["headers"] = headers
            captured["data"] = data
            captured["files"] = files
            return _Resp()

    monkeypatch.setattr("backend.services.voice.stt.httpx.AsyncClient", _Client)

    text = await transcribe_openai(
        _make_audio_b64(),
        sample_rate=16000,
        api_key="test-key",
        model="gpt-4o-mini-transcribe",
        language="en",
        timeout_seconds=7.5,
        max_retries=0,
    )

    assert text == "hello from openai"
    assert captured["url"] == "https://api.openai.com/v1/audio/transcriptions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["data"]["model"] == "gpt-4o-mini-transcribe"
    assert captured["data"]["language"] == "en"
    filename, payload, mime = captured["files"]["file"]
    assert filename.endswith(".wav")
    assert isinstance(payload, (bytes, bytearray))
    assert mime == "audio/wav"


@pytest.mark.asyncio
async def test_transcribe_file_openai_posts_file(monkeypatch):
    captured: dict = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"text": "ok file"}

    class _Client:
        def __init__(self, timeout: float):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, headers: dict, data: dict, files: dict):
            captured["url"] = url
            captured["headers"] = headers
            captured["data"] = data
            captured["files"] = files
            return _Resp()

    monkeypatch.setattr("backend.services.voice.stt.httpx.AsyncClient", _Client)
    fake_file_b64 = base64.b64encode(b"webm-data").decode()

    text = await transcribe_file_openai(
        fake_file_b64,
        mime_type="audio/webm",
        api_key="test-key",
        model="gpt-4o-mini-transcribe",
        timeout_seconds=5,
        max_retries=0,
    )

    assert text == "ok file"
    assert captured["data"]["model"] == "gpt-4o-mini-transcribe"
    filename, payload, mime = captured["files"]["file"]
    assert filename.endswith(".webm")
    assert payload == b"webm-data"
    assert mime == "audio/webm"


@pytest.mark.asyncio
async def test_transcribe_openai_records_estimated_cost(monkeypatch):
    captured: dict = {}

    async def _fake_openai_transcribe_bytes(**kwargs):
        return "ok"

    class _Tracker:
        def record_stt(self, **kwargs):
            captured.update(kwargs)

    from backend.app_state import app_state

    monkeypatch.setattr("backend.services.voice.stt._openai_transcribe_bytes", _fake_openai_transcribe_bytes)
    monkeypatch.setattr(app_state, "token_tracker", _Tracker())

    text = await transcribe_openai(
        _make_audio_b64(n_samples=16000),
        sample_rate=16000,
        api_key="test-key",
        cost_per_minute_usd=0.006,
        max_retries=0,
    )

    assert text == "ok"
    assert captured["stt_model"] == "gpt-4o-mini-transcribe"
    assert captured["audio_seconds"] == 1.0
    assert abs(captured["cost_usd"] - 0.0001) < 1e-9
