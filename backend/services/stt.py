"""STT service: async wrapper around shared/stt.py for use in the backend."""

from __future__ import annotations

import asyncio
import base64
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from shared.stt import FasterWhisperBackend


async def transcribe(
    audio_b64: str,
    sample_rate: int,
    model: FasterWhisperBackend,
    language: str | None = None,
    user_id: str = "",
) -> str:
    """
    Transcribe base64-encoded PCM float32 audio.

    Runs faster-whisper in a thread pool to avoid blocking the event loop.
    Records STT usage (audio seconds, transcription time) via app_state.token_tracker.
    """
    audio_bytes = base64.b64decode(audio_b64)
    return await asyncio.to_thread(
        _transcribe_sync, audio_bytes, sample_rate, model, language, user_id
    )


def _transcribe_sync(
    audio_bytes: bytes,
    sample_rate: int,
    model: FasterWhisperBackend,
    language: str | None,
    user_id: str = "",
) -> str:
    audio_np = np.frombuffer(audio_bytes, dtype=np.float32)
    audio_seconds = len(audio_np) / sample_rate
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_path = f.name
    t0 = time.monotonic()
    try:
        sf.write(tmp_path, audio_np, sample_rate)
        result = model.transcribe(tmp_path, language=language)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    try:
        from ..app_state import app_state
        app_state.token_tracker.record_stt(
            stt_model=getattr(model, "model_size", "unknown"),
            stt_language=language or "",
            audio_seconds=audio_seconds,
            transcription_ms=elapsed_ms,
            user_id=user_id,
        )
    except Exception:
        pass  # never let tracking break transcription

    return result


async def transcribe_file(
    audio_b64: str,
    mime_type: str,
    model: FasterWhisperBackend,
    language: str | None = None,
    user_id: str = "",
) -> str:
    """
    Transcribe base64-encoded audio in any format supported by ffmpeg (webm, mp4, ogg…).

    Writes raw bytes to a temp file with the correct extension so Whisper's ffmpeg
    backend can decode it — no float32 PCM conversion needed.
    """
    audio_bytes = base64.b64decode(audio_b64)
    ext = "." + mime_type.split("/")[-1].split(";")[0]  # audio/webm;codecs=opus → .webm
    return await asyncio.to_thread(
        _transcribe_file_sync, audio_bytes, ext, model, language, user_id
    )


def _transcribe_file_sync(
    audio_bytes: bytes,
    ext: str,
    model: FasterWhisperBackend,
    language: str | None,
    user_id: str = "",
) -> str:
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name
    t0 = time.monotonic()
    try:
        result = model.transcribe(tmp_path, language=language)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    # Audio duration unknown for encoded formats; approximate from byte size
    audio_seconds = len(audio_bytes) / 16000 / 2  # rough estimate

    try:
        from ..app_state import app_state
        app_state.token_tracker.record_stt(
            stt_model=getattr(model, "model_size", "unknown"),
            stt_language=language or "",
            audio_seconds=audio_seconds,
            transcription_ms=elapsed_ms,
            user_id=user_id,
        )
    except Exception:
        pass

    return result


def load_stt_model(model_size: str = "base") -> FasterWhisperBackend:
    """Load and return a FasterWhisperBackend (blocking; call at startup)."""
    return FasterWhisperBackend(model_size)
