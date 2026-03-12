"""STT service: async wrapper around shared/stt.py for use in the backend."""

from __future__ import annotations

import asyncio
import base64
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from shared.stt import FasterWhisperBackend


async def transcribe(
    audio_b64: str,
    sample_rate: int,
    model: FasterWhisperBackend,
    language: str | None = None,
) -> str:
    """
    Transcribe base64-encoded PCM float32 audio.

    Runs faster-whisper in a thread pool to avoid blocking the event loop.
    """
    audio_bytes = base64.b64decode(audio_b64)
    return await asyncio.to_thread(
        _transcribe_sync, audio_bytes, sample_rate, model, language
    )


def _transcribe_sync(
    audio_bytes: bytes,
    sample_rate: int,
    model: FasterWhisperBackend,
    language: str | None,
) -> str:
    audio_np = np.frombuffer(audio_bytes, dtype=np.float32)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_path = f.name
    try:
        sf.write(tmp_path, audio_np, sample_rate)
        return model.transcribe(tmp_path, language=language)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def load_stt_model(model_size: str = "base") -> FasterWhisperBackend:
    """Load and return a FasterWhisperBackend (blocking; call at startup)."""
    return FasterWhisperBackend(model_size)
