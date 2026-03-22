"""STT service: local Whisper and OpenAI transcription backends."""

from __future__ import annotations

import asyncio
import base64
import tempfile
import time
from io import BytesIO
from pathlib import Path

import numpy as np
import openai
import soundfile as sf

from .config import SUPPORTED_STT_PROVIDERS


class FasterWhisperBackend:
    """STT backend using faster-whisper (recommended; supports CUDA and CPU)."""

    def __init__(self, model_size: str):
        from faster_whisper import WhisperModel
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        print(f"Loading Faster-Whisper {model_size} on {device}...")
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)

    def transcribe(self, audio_path: str, language: str | None = None) -> str:
        segments, _ = self.model.transcribe(audio_path, beam_size=5, language=language)
        return " ".join(seg.text.strip() for seg in segments)


class WhisperXBackend:
    """STT backend using whisperx (requires a specific torch version)."""

    def __init__(self, model_size: str):
        import torch
        import whisperx
        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        print(f"Loading WhisperX {model_size} on {device}...")
        self.model = whisperx.load_model(model_size, device, compute_type=compute_type)
        self.device = device

    def transcribe(self, audio_path: str, language: str | None = None) -> str:
        import whisperx
        audio = whisperx.load_audio(audio_path)
        kwargs = {"batch_size": 16}
        if language:
            kwargs["language"] = language
        result = self.model.transcribe(audio, **kwargs)
        return " ".join(seg["text"].strip() for seg in result["segments"])


def select_stt_provider(explicit_provider: str | None) -> str:
    explicit = (explicit_provider or "").strip().lower()
    if explicit in SUPPORTED_STT_PROVIDERS:
        return explicit
    return "local"


async def transcribe(
    audio_b64: str,
    sample_rate: int,
    model: FasterWhisperBackend,
    language: str | None = None,
    user_id: str = "",
) -> str:
    """Transcribe base64-encoded PCM float32 audio via local Whisper."""
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
        from ...app_state import app_state
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


async def transcribe_file(
    audio_b64: str,
    mime_type: str,
    model: FasterWhisperBackend,
    language: str | None = None,
    user_id: str = "",
) -> str:
    """Transcribe base64-encoded audio in any format supported by ffmpeg."""
    audio_bytes = base64.b64decode(audio_b64)
    ext = "." + mime_type.split("/")[-1].split(";")[0]
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
    audio_seconds = len(audio_bytes) / 16000 / 2  # rough estimate

    try:
        from ...app_state import app_state
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


async def transcribe_openai(
    audio_b64: str,
    sample_rate: int,
    *,
    api_key: str | None,
    model: str = "gpt-4o-mini-transcribe",
    language: str | None = None,
    timeout_seconds: float = 30.0,
    max_retries: int = 1,
    cost_per_minute_usd: float = 0.0,
    user_id: str = "",
) -> str:
    """Transcribe base64 float32 PCM audio with OpenAI STT."""
    if not (api_key or "").strip():
        raise RuntimeError("OPENAI_API_KEY is not configured for OpenAI STT.")

    audio_bytes = base64.b64decode(audio_b64)
    audio_np = np.frombuffer(audio_bytes, dtype=np.float32)
    audio_seconds = len(audio_np) / sample_rate

    buf = BytesIO()
    sf.write(buf, audio_np, sample_rate, format="WAV")
    wav_bytes = buf.getvalue()

    t0 = time.monotonic()
    client = openai.AsyncOpenAI(
        api_key=(api_key or "").strip(),
        timeout=timeout_seconds,
        max_retries=max_retries,
    )
    kwargs: dict = {"model": model, "file": ("audio.wav", wav_bytes, "audio/wav")}
    if language:
        kwargs["language"] = language
    response = await client.audio.transcriptions.create(**kwargs)
    text = response.text.strip()

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    estimated_cost = (audio_seconds / 60.0) * max(0.0, cost_per_minute_usd)

    try:
        from ...app_state import app_state
        app_state.token_tracker.record_stt(
            stt_model=model,
            stt_language=language or "",
            audio_seconds=audio_seconds,
            transcription_ms=elapsed_ms,
            cost_usd=estimated_cost,
            user_id=user_id,
        )
    except Exception:
        pass

    return text


async def transcribe_file_openai(
    audio_b64: str,
    mime_type: str,
    *,
    api_key: str | None,
    model: str = "gpt-4o-mini-transcribe",
    language: str | None = None,
    timeout_seconds: float = 30.0,
    max_retries: int = 1,
    cost_per_minute_usd: float = 0.0,
    user_id: str = "",
) -> str:
    """Transcribe base64 encoded audio file with OpenAI STT."""
    if not (api_key or "").strip():
        raise RuntimeError("OPENAI_API_KEY is not configured for OpenAI STT.")

    audio_bytes = base64.b64decode(audio_b64)
    ext = "." + mime_type.split("/")[-1].split(";")[0]
    filename = f"audio{ext}"

    t0 = time.monotonic()
    client = openai.AsyncOpenAI(
        api_key=(api_key or "").strip(),
        timeout=timeout_seconds,
        max_retries=max_retries,
    )
    kwargs: dict = {"model": model, "file": (filename, audio_bytes, mime_type)}
    if language:
        kwargs["language"] = language
    response = await client.audio.transcriptions.create(**kwargs)
    text = response.text.strip()

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    audio_seconds = len(audio_bytes) / 16000 / 2
    estimated_cost = (audio_seconds / 60.0) * max(0.0, cost_per_minute_usd)

    try:
        from ...app_state import app_state
        app_state.token_tracker.record_stt(
            stt_model=model,
            stt_language=language or "",
            audio_seconds=audio_seconds,
            transcription_ms=elapsed_ms,
            cost_usd=estimated_cost,
            user_id=user_id,
        )
    except Exception:
        pass

    return text


def load_stt_model(model_size: str = "base") -> FasterWhisperBackend:
    """Load and return a FasterWhisperBackend (blocking; call at startup)."""
    return FasterWhisperBackend(model_size)
