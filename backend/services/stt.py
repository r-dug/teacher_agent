"""STT service: async wrapper around shared/stt.py for use in the backend."""

from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import time
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf

from shared.stt import FasterWhisperBackend

SUPPORTED_STT_PROVIDERS = frozenset({"local", "openai"})
OPENAI_STT_MODELS = [
    "gpt-4o-mini-transcribe",
    "gpt-4o-transcribe",
    "whisper-1",
]


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
    """
    Transcribe base64 float32 PCM audio with OpenAI STT.
    """
    if not (api_key or "").strip():
        raise RuntimeError("OPENAI_API_KEY is not configured for OpenAI STT.")

    audio_bytes = base64.b64decode(audio_b64)
    audio_np = np.frombuffer(audio_bytes, dtype=np.float32)
    audio_seconds = len(audio_np) / sample_rate

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_path = f.name
    t0 = time.monotonic()
    try:
        sf.write(tmp_path, audio_np, sample_rate)
        wav_bytes = Path(tmp_path).read_bytes()
        text = await _openai_transcribe_bytes(
            audio_bytes=wav_bytes,
            filename="audio.wav",
            mime_type="audio/wav",
            api_key=api_key,
            model=model,
            language=language,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    estimated_cost = (audio_seconds / 60.0) * max(0.0, cost_per_minute_usd)

    try:
        from ..app_state import app_state
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
    """
    Transcribe base64 encoded audio file with OpenAI STT.
    """
    if not (api_key or "").strip():
        raise RuntimeError("OPENAI_API_KEY is not configured for OpenAI STT.")

    audio_bytes = base64.b64decode(audio_b64)
    ext = "." + mime_type.split("/")[-1].split(";")[0]
    filename = f"audio{ext}"
    t0 = time.monotonic()
    text = await _openai_transcribe_bytes(
        audio_bytes=audio_bytes,
        filename=filename,
        mime_type=mime_type,
        api_key=api_key,
        model=model,
        language=language,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    # Encoded format duration is not always cheap to decode; use rough estimate.
    audio_seconds = len(audio_bytes) / 16000 / 2
    estimated_cost = (audio_seconds / 60.0) * max(0.0, cost_per_minute_usd)

    try:
        from ..app_state import app_state
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


async def _openai_transcribe_bytes(
    *,
    audio_bytes: bytes,
    filename: str,
    mime_type: str,
    api_key: str | None,
    model: str,
    language: str | None,
    timeout_seconds: float,
    max_retries: int,
) -> str:
    """
    Upload an audio file to OpenAI `/v1/audio/transcriptions` and return text.
    """
    headers = {"Authorization": f"Bearer {(api_key or '').strip()}"}
    data: dict[str, str] = {"model": model}
    if language:
        data["language"] = language

    last_err: Exception | None = None
    for attempt in range(max(0, max_retries) + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers=headers,
                    data=data,
                    files={"file": (filename, audio_bytes, mime_type)},
                )
            if resp.status_code >= 400:
                raise RuntimeError(_format_openai_error(resp))
            payload = resp.json()
            text = payload.get("text", "")
            if not isinstance(text, str):
                raise RuntimeError("Unexpected OpenAI STT response: missing text field")
            return text.strip()
        except Exception as exc:
            last_err = exc
            if attempt >= max(0, max_retries):
                break
            await asyncio.sleep(min(0.2 * (2**attempt), 1.0))

    raise RuntimeError(f"OpenAI STT failed: {last_err}") from last_err


def _format_openai_error(response: httpx.Response) -> str:
    try:
        data = response.json()
    except json.JSONDecodeError:
        return f"{response.status_code} {response.text[:300]}"
    message = data.get("error", {}).get("message")
    if message:
        return f"{response.status_code} {message}"
    return f"{response.status_code} {str(data)[:300]}"


def load_stt_model(model_size: str = "base") -> FasterWhisperBackend:
    """Load and return a FasterWhisperBackend (blocking; call at startup)."""
    return FasterWhisperBackend(model_size)
