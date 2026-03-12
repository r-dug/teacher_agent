"""Voice and STT language listing endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from shared.constants import KOKORO_VOICES, DEFAULT_KOKORO_VOICE
from shared.phonetics import WHISPER_LANGUAGES

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
    return [
        VoiceResponse(
            id=name,
            lang_code=code,
            is_default=(name == DEFAULT_KOKORO_VOICE),
        )
        for name, code in KOKORO_VOICES.items()
    ]


@router.get("/stt-languages", response_model=list[LanguageResponse])
async def list_stt_languages():
    return [
        LanguageResponse(name=name, code=code)
        for name, code in WHISPER_LANGUAGES.items()
    ]
