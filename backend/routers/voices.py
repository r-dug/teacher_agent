"""Voice and STT language listing endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from shared.constants import KOKORO_VOICES, DEFAULT_KOKORO_VOICE
from shared.phonetics import WHISPER_LANGUAGES
from ..app_state import app_state
from ..config import settings
from ..services.stt import OPENAI_STT_MODELS, select_stt_provider

router = APIRouter(tags=["voices"])


class VoiceResponse(BaseModel):
    id: str
    lang_code: str
    is_default: bool


class LanguageResponse(BaseModel):
    name: str
    code: str | None  # None = auto-detect


@router.get("/voices", response_model=list[VoiceResponse])
async def list_voices():
    provider = app_state.tts_provider
    voices = (
        provider.list_voices()
        if provider is not None and hasattr(provider, "list_voices")
        else KOKORO_VOICES
    )
    default_voice = (
        getattr(provider, "default_voice", "") if provider is not None else DEFAULT_KOKORO_VOICE
    ) or DEFAULT_KOKORO_VOICE
    return [
        VoiceResponse(
            id=name,
            lang_code=code,
            is_default=(name == default_voice),
        )
        for name, code in voices.items()
    ]


@router.get("/stt-languages", response_model=list[LanguageResponse])
async def list_stt_languages():
    return [
        LanguageResponse(name=name, code=code)
        for name, code in WHISPER_LANGUAGES.items()
    ]


STT_MODEL_SIZES = ["tiny", "base", "small", "medium", "large-v2", "large-v3"]


class SttModelResponse(BaseModel):
    id: str
    is_default: bool


class SttProviderResponse(BaseModel):
    id: str
    is_default: bool


class VoiceArchResponse(BaseModel):
    id: str
    label: str
    is_default: bool


@router.get("/stt-providers", response_model=list[SttProviderResponse])
async def list_stt_providers():
    default_provider = settings.effective_stt_provider()
    providers = ["local", "openai"]
    return [
        SttProviderResponse(id=provider_id, is_default=(provider_id == default_provider))
        for provider_id in providers
    ]


@router.get("/voice-arches", response_model=list[VoiceArchResponse])
async def list_voice_arches():
    default_arch = settings.effective_voice_arch()
    options = [
        ("chained", "Chained (STT -> LLM -> TTS)"),
        ("realtime", "Realtime (OpenAI audio in/out)"),
    ]
    return [
        VoiceArchResponse(id=arch_id, label=label, is_default=(arch_id == default_arch))
        for arch_id, label in options
    ]


@router.get("/stt-models", response_model=list[SttModelResponse])
async def list_stt_models(provider: str | None = Query(default=None)):
    effective_provider = select_stt_provider(provider) if provider else settings.effective_stt_provider()
    if effective_provider == "openai":
        default_model = settings.OPENAI_STT_MODEL
        return [
            SttModelResponse(id=model_id, is_default=(model_id == default_model))
            for model_id in OPENAI_STT_MODELS
        ]

    return [
        SttModelResponse(id=size, is_default=(size == settings.STT_MODEL_SIZE))
        for size in STT_MODEL_SIZES
    ]
