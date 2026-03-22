"""Backend configuration loaded from environment variables."""

from __future__ import annotations

import os
from pathlib import Path


class Settings:
    # Server
    HOST: str = os.getenv("BACKEND_HOST", "127.0.0.1")
    PORT: int = int(os.getenv("BACKEND_PORT", "8001"))

    # Storage
    STORAGE_DIR: Path = Path(os.getenv("STORAGE_DIR", "./storage"))
    DB_PATH: Path = Path(os.getenv("DB_PATH", "./storage/db.sqlite3"))

    # Models
    STT_MODEL_SIZE: str = os.getenv("STT_MODEL_SIZE", "base")
    STT_PROVIDER: str | None = os.getenv("STT_PROVIDER")
    OPENAI_STT_MODEL: str = os.getenv("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")
    OPENAI_STT_TIMEOUT_S: float = float(os.getenv("OPENAI_STT_TIMEOUT_S", "30"))
    OPENAI_STT_MAX_RETRIES: int = int(os.getenv("OPENAI_STT_MAX_RETRIES", "1"))
    OPENAI_STT_COST_PER_MINUTE_USD: float = float(
        os.getenv("OPENAI_STT_COST_PER_MINUTE_USD", "0.006")
    )
    LLM_MODEL: str = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
    TEACH_LLM_PROVIDER: str = os.getenv("TEACH_LLM_PROVIDER", "anthropic")
    TEACH_LLM_MODEL: str | None = os.getenv("TEACH_LLM_MODEL")
    DECOMPOSE_LLM_PROVIDER: str | None = os.getenv("DECOMPOSE_LLM_PROVIDER")
    DECOMPOSE_LLM_MODEL: str | None = os.getenv("DECOMPOSE_LLM_MODEL")
    AUTHORING_LLM_PROVIDER: str | None = os.getenv("AUTHORING_LLM_PROVIDER")
    AUTHORING_LLM_MODEL: str | None = os.getenv("AUTHORING_LLM_MODEL")
    OPENAI_LLM_TIMEOUT_S: float = float(os.getenv("OPENAI_LLM_TIMEOUT_S", "30"))
    OPENAI_LLM_MAX_RETRIES: int = int(os.getenv("OPENAI_LLM_MAX_RETRIES", "1"))
    OPENAI_DECOMPOSE_TIMEOUT_S: float = float(os.getenv("OPENAI_DECOMPOSE_TIMEOUT_S", "45"))
    OPENAI_DECOMPOSE_MAX_RETRIES: int = int(os.getenv("OPENAI_DECOMPOSE_MAX_RETRIES", "1"))
    OPENAI_DECOMPOSE_MAX_INPUT_CHARS: int = int(
        os.getenv("OPENAI_DECOMPOSE_MAX_INPUT_CHARS", "120000")
    )
    OPENAI_DECOMPOSE_ENABLE_VISION_OCR: bool = (
        os.getenv("OPENAI_DECOMPOSE_ENABLE_VISION_OCR", "true").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    DEFAULT_VOICE: str = os.getenv("DEFAULT_VOICE", "af_heart")
    ENV: str = os.getenv("ENV", "development")
    TTS_PROVIDER: str | None = os.getenv("TTS_PROVIDER")
    OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")
    OPENAI_TTS_MODEL: str = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
    OPENAI_TTS_VOICE: str = os.getenv("OPENAI_TTS_VOICE", "alloy")
    OPENAI_TTS_FORMAT: str = os.getenv("OPENAI_TTS_FORMAT", "wav")
    OPENAI_TTS_TIMEOUT_S: float = float(os.getenv("OPENAI_TTS_TIMEOUT_S", "20"))
    OPENAI_TTS_MAX_RETRIES: int = int(os.getenv("OPENAI_TTS_MAX_RETRIES", "1"))
    OPENAI_TTS_COST_PER_MINUTE_USD: float = float(
        os.getenv("OPENAI_TTS_COST_PER_MINUTE_USD", "0.015")
    )
    VOICE_ARCH: str = os.getenv("VOICE_ARCH", "chained")
    OPENAI_REALTIME_MODEL: str = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-mini")
    OPENAI_REALTIME_VOICE: str = os.getenv("OPENAI_REALTIME_VOICE", "alloy")
    OPENAI_REALTIME_SAMPLE_RATE: int = int(os.getenv("OPENAI_REALTIME_SAMPLE_RATE", "24000"))
    OPENAI_REALTIME_TIMEOUT_S: float = float(os.getenv("OPENAI_REALTIME_TIMEOUT_S", "30"))
    OPENAI_REALTIME_MAX_RETRIES: int = int(os.getenv("OPENAI_REALTIME_MAX_RETRIES", "1"))
    OPENAI_REALTIME_VAD_THRESHOLD: float = float(os.getenv("OPENAI_REALTIME_VAD_THRESHOLD", "0.5"))
    OPENAI_REALTIME_VAD_PREFIX_MS: int = int(os.getenv("OPENAI_REALTIME_VAD_PREFIX_MS", "400"))
    OPENAI_REALTIME_VAD_SILENCE_MS: int = int(os.getenv("OPENAI_REALTIME_VAD_SILENCE_MS", "1500"))
    OPENAI_REALTIME_INTERRUPT_RESPONSE: bool = (
        os.getenv("OPENAI_REALTIME_INTERRUPT_RESPONSE", "false").strip().lower() in {"1", "true", "yes", "on"}
    )

    # Image generation
    IMAGE_GEN_ENABLE: bool = (
        os.getenv("IMAGE_GEN_ENABLE", "false").strip().lower() in {"1", "true", "yes", "on"}
    )
    IMAGE_GEN_PROVIDER: str = os.getenv("IMAGE_GEN_PROVIDER", "openai")
    IMAGE_GEN_MODEL: str = os.getenv("IMAGE_GEN_MODEL", "dall-e-3")
    IMAGE_GEN_SIZE: str = os.getenv("IMAGE_GEN_SIZE", "1024x1024")
    IMAGE_GEN_QUALITY: str = os.getenv("IMAGE_GEN_QUALITY", "standard")
    IMAGE_GEN_TIMEOUT_S: float = float(os.getenv("IMAGE_GEN_TIMEOUT_S", "120"))
    IMAGE_GEN_MAX_RETRIES: int = int(os.getenv("IMAGE_GEN_MAX_RETRIES", "1"))
    # Style prefix prepended to the raw prompt before sending to the image API.
    IMAGE_GEN_STYLE_PREFIX: str = os.getenv(
        "IMAGE_GEN_STYLE_PREFIX",
        "Clear educational diagram, labelled, white background: ",
    )

    # Auth (prototype: all requests trusted from the frontend server)
    FRONTEND_ORIGIN: str = os.getenv("FRONTEND_ORIGIN", "http://127.0.0.1:8000")
    # Shared secret for BFF→backend calls.  When set, all inbound requests must
    # carry X-Internal-Token: <secret>.  Unset in dev (no check performed).
    BACKEND_SHARED_SECRET: str | None = os.getenv("BACKEND_SHARED_SECRET")

    def effective_tts_provider(self) -> str:
        from .services.voice.tts import select_tts_provider

        return select_tts_provider(self.TTS_PROVIDER, self.ENV)

    def effective_stt_provider(self) -> str:
        from .services.voice.stt import select_stt_provider

        return select_stt_provider(self.STT_PROVIDER)

    def default_tts_voice(self) -> str:
        if self.effective_tts_provider() == "openai":
            return self.OPENAI_TTS_VOICE
        return self.DEFAULT_VOICE

    def effective_voice_arch(self) -> str:
        arch = (self.VOICE_ARCH or "").strip().lower()
        if arch in {"realtime", "chained"}:
            return arch
        return "chained"

    def effective_decompose_llm_provider(self) -> str:
        provider = (self.DECOMPOSE_LLM_PROVIDER or self.TEACH_LLM_PROVIDER or "anthropic").strip().lower()
        if provider in {"anthropic", "openai"}:
            return provider
        return "anthropic"

    def effective_decompose_llm_model(self) -> str:
        explicit = (self.DECOMPOSE_LLM_MODEL or "").strip()
        if explicit:
            return explicit
        if self.effective_decompose_llm_provider() == "openai":
            return "gpt-4o-mini"
        return self.LLM_MODEL

    def effective_authoring_llm_provider(self) -> str:
        """Provider for advisor chat + TOC extraction. Falls back to decompose → teach → anthropic."""
        provider = (
            self.AUTHORING_LLM_PROVIDER
            or self.DECOMPOSE_LLM_PROVIDER
            or self.TEACH_LLM_PROVIDER
            or "anthropic"
        ).strip().lower()
        if provider in {"anthropic", "openai"}:
            return provider
        return "anthropic"

    def effective_authoring_llm_model(self) -> str:
        explicit = (self.AUTHORING_LLM_MODEL or "").strip()
        if explicit:
            return explicit
        if self.effective_authoring_llm_provider() == "openai":
            return "gpt-4o-mini"
        return self.LLM_MODEL

    def ensure_dirs(self) -> None:
        self.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
