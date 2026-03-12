"""Tests for the STT service (mocked model)."""

from __future__ import annotations

import base64
import struct
import pytest
from unittest.mock import MagicMock

from backend.services.stt import transcribe


def _make_audio_b64(n_samples: int = 1600, sample_rate: int = 16000) -> str:
    """Generate a base64-encoded silent float32 PCM audio blob."""
    import numpy as np
    audio = np.zeros(n_samples, dtype=np.float32)
    return base64.b64encode(audio.tobytes()).decode()


@pytest.mark.asyncio
async def test_transcribe_calls_model(tmp_path, monkeypatch):
    """transcribe() should write a temp WAV and call model.transcribe()."""
    mock_model = MagicMock()
    mock_model.transcribe.return_value = "hello world"

    audio_b64 = _make_audio_b64()
    result = await transcribe(audio_b64, sample_rate=16000, model=mock_model)

    assert result == "hello world"
    mock_model.transcribe.assert_called_once()
    # The temp file path argument should end in .wav
    call_args = mock_model.transcribe.call_args
    assert call_args[0][0].endswith(".wav")


@pytest.mark.asyncio
async def test_transcribe_passes_language(tmp_path):
    mock_model = MagicMock()
    mock_model.transcribe.return_value = "bonjour"

    audio_b64 = _make_audio_b64()
    result = await transcribe(audio_b64, sample_rate=16000, model=mock_model, language="fr")

    assert result == "bonjour"
    _, kwargs = mock_model.transcribe.call_args
    assert kwargs.get("language") == "fr"


@pytest.mark.asyncio
async def test_transcribe_cleans_up_temp_file(tmp_path, monkeypatch):
    """The temp WAV file should be deleted after transcription."""
    import os
    created_paths: list[str] = []
    original_write = None

    import soundfile as sf
    original_write = sf.write

    def tracking_write(path, *args, **kwargs):
        created_paths.append(path)
        return original_write(path, *args, **kwargs)

    monkeypatch.setattr("backend.services.stt.sf.write", tracking_write)

    mock_model = MagicMock()
    mock_model.transcribe.return_value = "test"

    await transcribe(_make_audio_b64(), 16000, mock_model)

    for path in created_paths:
        assert not os.path.exists(path), f"Temp file was not cleaned up: {path}"
