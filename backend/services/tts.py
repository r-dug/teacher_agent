"""TTS services and provider adapters for backend runtime synthesis."""

from __future__ import annotations

import json
import time
from io import BytesIO
from dataclasses import dataclass

import httpx
import numpy as np

from shared.constants import (
    DEFAULT_KOKORO_VOICE,
    DEFAULT_OPENAI_TTS_VOICE,
    KOKORO_SAMPLE_RATE,
    KOKORO_VOICES,
    OPENAI_TTS_SAMPLE_RATE,
    OPENAI_TTS_VOICES,
)

SUPPORTED_TTS_PROVIDERS = frozenset({"kokoro", "openai"})


def select_tts_provider(explicit_provider: str | None, env_name: str | None) -> str:
    """
    Resolve the active TTS provider from env/config.

    Priority:
      1. explicit `TTS_PROVIDER` override if valid
      2. auto mapping: `ENV=production` -> kokoro, otherwise openai
    """
    explicit = (explicit_provider or "").strip().lower()
    if explicit in SUPPORTED_TTS_PROVIDERS:
        return explicit
    if (env_name or "").strip().lower() == "production":
        return "kokoro"
    return "openai"


@dataclass(slots=True)
class TTSSynthesisResult:
    """Normalized synthesis output consumed by the teaching turn pipeline."""

    audio: np.ndarray
    sample_rate: int
    voice: str
    characters: int
    synthesis_ms: int
    estimated_cost_usd: float = 0.0


class KokoroTTSProvider:
    """Adapter around the local Kokoro pipeline."""

    provider_name = "kokoro"
    requires_preprocessing = True

    def __init__(
        self,
        pipeline,
        default_voice: str = DEFAULT_KOKORO_VOICE,
    ) -> None:
        self._pipeline = pipeline
        self.default_voice = default_voice if default_voice in KOKORO_VOICES else DEFAULT_KOKORO_VOICE

    def list_voices(self) -> dict[str, str]:
        return dict(KOKORO_VOICES)

    def resolve_voice(self, voice: str | None) -> str:
        if voice and voice in KOKORO_VOICES:
            return voice
        return self.default_voice

    def synthesize(self, text: str, voice: str | None = None) -> TTSSynthesisResult:
        if self._pipeline is None:
            raise RuntimeError("Kokoro pipeline is not loaded.")

        resolved_voice = self.resolve_voice(voice)
        t0 = time.monotonic()
        chunks: list[np.ndarray] = []
        for _, _, audio in self._pipeline(text, voice=resolved_voice):
            data = audio.numpy() if hasattr(audio, "numpy") else np.asarray(audio)
            chunks.append(np.clip(data, -1.0, 1.0).astype(np.float32))
        synthesis_ms = int((time.monotonic() - t0) * 1000)
        combined = np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(0, dtype=np.float32)
        return TTSSynthesisResult(
            audio=combined,
            sample_rate=KOKORO_SAMPLE_RATE,
            voice=resolved_voice,
            characters=len(text),
            synthesis_ms=synthesis_ms,
            estimated_cost_usd=0.0,
        )


class OpenAITTSProvider:
    """
    Adapter for OpenAI TTS (`/v1/audio/speech`).

    Uses PCM output and normalizes to float32 mono samples in [-1, 1].
    """

    provider_name = "openai"
    requires_preprocessing = False

    def __init__(
        self,
        api_key: str | None,
        model: str = "gpt-4o-mini-tts",
        default_voice: str = DEFAULT_OPENAI_TTS_VOICE,
        response_format: str = "wav",
        timeout_seconds: float = 20.0,
        max_retries: int = 1,
        cost_per_minute_usd: float = 0.015,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self.model = model
        self.default_voice = (
            default_voice if default_voice in OPENAI_TTS_VOICES else DEFAULT_OPENAI_TTS_VOICE
        )
        self.response_format = (response_format or "wav").strip().lower()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self.cost_per_minute_usd = max(0.0, cost_per_minute_usd)

    def list_voices(self) -> dict[str, str]:
        return dict(OPENAI_TTS_VOICES)

    def resolve_voice(self, voice: str | None) -> str:
        if voice and voice in OPENAI_TTS_VOICES:
            return voice
        return self.default_voice

    def synthesize(self, text: str, voice: str | None = None) -> TTSSynthesisResult:
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured for OpenAI TTS.")

        resolved_voice = self.resolve_voice(voice)
        payload = {
            "model": self.model,
            "voice": resolved_voice,
            "input": text,
            "format": self.response_format,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}

        last_err: Exception | None = None
        t0 = time.monotonic()
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(
                        "https://api.openai.com/v1/audio/speech",
                        headers=headers,
                        json=payload,
                    )
                if response.status_code >= 400:
                    raise RuntimeError(_format_openai_error(response))
                audio, sample_rate = _decode_openai_audio(
                    response.content,
                    expected_format=self.response_format,
                )
                synthesis_ms = int((time.monotonic() - t0) * 1000)
                audio_seconds = len(audio) / max(1, sample_rate)
                estimated_cost = (audio_seconds / 60.0) * self.cost_per_minute_usd
                return TTSSynthesisResult(
                    audio=np.clip(audio, -1.0, 1.0),
                    sample_rate=sample_rate,
                    voice=resolved_voice,
                    characters=len(text),
                    synthesis_ms=synthesis_ms,
                    estimated_cost_usd=estimated_cost,
                )
            except Exception as exc:  # pragma: no cover - retry path is tested via behavior
                last_err = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(min(0.2 * (2**attempt), 1.0))

        raise RuntimeError(f"OpenAI TTS failed: {last_err}") from last_err


def build_tts_providers(
    *,
    selected_provider: str,
    kokoro_pipeline,
    default_kokoro_voice: str,
    openai_api_key: str | None,
    openai_model: str,
    openai_voice: str,
    openai_format: str,
    openai_timeout_seconds: float,
    openai_max_retries: int,
    openai_cost_per_minute_usd: float,
) -> tuple[object, object | None]:
    """
    Build primary and optional fallback providers for runtime use.

    Returns: (primary_provider, fallback_provider)
    """
    kokoro_provider = KokoroTTSProvider(
        pipeline=kokoro_pipeline,
        default_voice=default_kokoro_voice,
    )
    if selected_provider == "openai":
        primary = OpenAITTSProvider(
            api_key=openai_api_key,
            model=openai_model,
            default_voice=openai_voice,
            response_format=openai_format,
            timeout_seconds=openai_timeout_seconds,
            max_retries=openai_max_retries,
            cost_per_minute_usd=openai_cost_per_minute_usd,
        )
        return primary, kokoro_provider
    return kokoro_provider, None


def load_kokoro_pipeline(voice: str = DEFAULT_KOKORO_VOICE):
    """
    Load and return a KPipeline for the given voice (blocking; call at startup).

    The pipeline is language-code scoped.  When sessions request different voices
    with the same language code, the same pipeline instance can be reused.
    For simplicity the backend loads a single pipeline at startup using the
    default voice's language code; the lang_code is 'a' (American English) for
    all default voices.
    """
    from kokoro import KPipeline

    lang_code = KOKORO_VOICES.get(voice, "a")
    return KPipeline(lang_code=lang_code)


def _format_openai_error(response: httpx.Response) -> str:
    """Best-effort error extraction for OpenAI HTTP failures."""
    try:
        data = response.json()
    except json.JSONDecodeError:
        return f"{response.status_code} {response.text[:300]}"
    message = data.get("error", {}).get("message")
    if message:
        return f"{response.status_code} {message}"
    return f"{response.status_code} {str(data)[:300]}"


def _decode_openai_audio(content: bytes, expected_format: str) -> tuple[np.ndarray, int]:
    """
    Decode OpenAI audio/speech payload.

    Handles both raw 16-bit PCM and WAV container responses to avoid noisy output
    if the API responds with a container unexpectedly.
    """
    expected = (expected_format or "").strip().lower()

    # Explicit raw PCM path.
    if expected == "pcm16":
        pcm_i16 = np.frombuffer(content, dtype="<i2")
        audio = (pcm_i16.astype(np.float32) / 32768.0).astype(np.float32)
        return audio, OPENAI_TTS_SAMPLE_RATE

    # Container path (wav/mp3/opus/flac/...)
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WAVE":
        pass  # Fast path handled by soundfile below.

    try:
        import soundfile as sf
    except Exception as exc:  # pragma: no cover - dependency is present in project
        raise RuntimeError("Received container audio but soundfile is unavailable.") from exc
    try:
        audio, sample_rate = sf.read(BytesIO(content), dtype="float32")
        if isinstance(audio, np.ndarray) and audio.ndim > 1:
            audio = audio.mean(axis=1)
        return np.asarray(audio, dtype=np.float32), int(sample_rate)
    except Exception as exc:
        raise RuntimeError(
            f"Could not decode OpenAI TTS audio as '{expected or 'container'}'. "
            "Set OPENAI_TTS_FORMAT=wav or pcm16."
        ) from exc
