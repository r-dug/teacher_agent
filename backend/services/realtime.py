"""OpenAI Realtime voice-turn helpers (audio in/out over WebSocket)."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import SimpleNamespace
from urllib.parse import quote

import numpy as np
from websockets.asyncio.client import connect


@dataclass(slots=True)
class RealtimeTurnSummary:
    user_transcript: str
    assistant_text: str
    usage: dict


def float32_b64_to_pcm16_b64(
    audio_b64: str,
    sample_rate: int,
    target_sample_rate: int = 24000,
) -> str:
    """Convert base64 float32 PCM audio to base64 little-endian PCM16."""
    raw = base64.b64decode(audio_b64)
    audio = np.frombuffer(raw, dtype=np.float32)
    if sample_rate != target_sample_rate and audio.size > 0:
        audio = _resample_linear(audio, sample_rate, target_sample_rate)
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    return base64.b64encode(pcm16.tobytes()).decode()


def pcm16_b64_to_float32(b64_pcm16: str) -> np.ndarray:
    """Convert base64 little-endian PCM16 bytes to float32 samples."""
    raw = base64.b64decode(b64_pcm16)
    pcm = np.frombuffer(raw, dtype="<i2")
    return (pcm.astype(np.float32) / 32768.0).astype(np.float32)


def usage_from_realtime(usage: dict) -> object:
    """
    Normalize OpenAI realtime usage payload to a tracker-compatible object.
    """
    input_details = usage.get("input_token_details") or {}
    return SimpleNamespace(
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_read_input_tokens=int(input_details.get("cached_tokens") or 0),
        cache_creation_input_tokens=0,
    )


async def run_realtime_voice_turn(
    *,
    audio_b64: str,
    sample_rate: int,
    api_key: str | None,
    model: str,
    voice: str,
    instructions: str = "",
    target_sample_rate: int = 24000,
    timeout_seconds: float = 30.0,
    max_retries: int = 1,
    on_user_transcript: Callable[[str], Awaitable[None]] | None = None,
    on_text_delta: Callable[[str], Awaitable[None]] | None = None,
    on_audio_chunk: Callable[[np.ndarray], Awaitable[None]] | None = None,
) -> RealtimeTurnSummary:
    """
    Send one utterance to OpenAI Realtime and stream transcript/text/audio deltas.

    This is a single-turn helper: caller controls conversational history and
    persistence on success/failure.
    """
    if not (api_key or "").strip():
        raise RuntimeError("OPENAI_API_KEY is not configured for realtime voice.")

    headers = {
        "Authorization": f"Bearer {(api_key or '').strip()}",
        "OpenAI-Beta": "realtime=v1",
    }
    ws_url = f"wss://api.openai.com/v1/realtime?model={quote(model)}"
    input_audio = float32_b64_to_pcm16_b64(audio_b64, sample_rate, target_sample_rate)

    last_err: Exception | None = None
    for attempt in range(max(0, max_retries) + 1):
        try:
            async with connect(
                ws_url,
                additional_headers=headers,
                open_timeout=timeout_seconds,
                close_timeout=timeout_seconds,
                max_size=None,
            ) as ws:
                session_update = {
                    "type": "session.update",
                    "session": {
                        "instructions": instructions,
                        "voice": voice,
                        "input_audio_format": "pcm16",
                        "output_audio_format": "pcm16",
                        "modalities": ["text", "audio"],
                        "turn_detection": {
                            "type": "server_vad",
                            "create_response": True,
                        },
                        "input_audio_transcription": {"model": "gpt-4o-mini-transcribe"},
                    },
                }
                await ws.send(json.dumps(session_update))
                await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": input_audio}))
                await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                await ws.send(json.dumps({
                    "type": "response.create",
                    "response": {"modalities": ["text", "audio"]},
                }))

                user_transcript = ""
                assistant_text = ""
                usage: dict = {}
                t0 = time.monotonic()

                while True:
                    # Keep an upper bound so socket hangs don't stall a turn forever.
                    wait_s = max(5.0, timeout_seconds - min(timeout_seconds - 1.0, time.monotonic() - t0))
                    raw = await asyncio.wait_for(ws.recv(), timeout=wait_s)
                    event = json.loads(raw)
                    etype = event.get("type", "")

                    if etype == "conversation.item.input_audio_transcription.completed":
                        transcript = (event.get("transcript") or "").strip()
                        if transcript:
                            user_transcript = transcript
                            if on_user_transcript:
                                await on_user_transcript(transcript)

                    elif etype in {
                        "response.output_text.delta",
                        "response.text.delta",
                        "response.audio_transcript.delta",
                        "response.output_audio_transcript.delta",
                    }:
                        delta = event.get("delta", "")
                        if isinstance(delta, str) and delta:
                            assistant_text += delta
                            if on_text_delta:
                                await on_text_delta(delta)

                    elif etype in {"response.output_audio.delta", "response.audio.delta"}:
                        delta_b64 = event.get("delta", "")
                        if isinstance(delta_b64, str) and delta_b64:
                            chunk = pcm16_b64_to_float32(delta_b64)
                            if on_audio_chunk:
                                await on_audio_chunk(chunk)

                    elif etype == "response.done":
                        response = event.get("response") or {}
                        usage = response.get("usage") or {}
                        break

                    elif etype == "error":
                        err = event.get("error") or {}
                        message = err.get("message") or str(err) or "Unknown realtime error"
                        raise RuntimeError(message)

                return RealtimeTurnSummary(
                    user_transcript=user_transcript,
                    assistant_text=assistant_text.strip(),
                    usage=usage,
                )
        except Exception as exc:
            last_err = exc
            if attempt >= max(0, max_retries):
                break
            await asyncio.sleep(min(0.2 * (2**attempt), 1.0))

    raise RuntimeError(f"OpenAI realtime turn failed: {last_err}") from last_err


def _resample_linear(audio: np.ndarray, from_sr: int, to_sr: int) -> np.ndarray:
    """Lightweight linear resampler (sufficient for speech turn payloads)."""
    if audio.size == 0 or from_sr <= 0 or to_sr <= 0 or from_sr == to_sr:
        return audio.astype(np.float32, copy=False)
    duration = audio.size / float(from_sr)
    out_n = max(1, int(round(duration * to_sr)))
    x_old = np.linspace(0.0, duration, num=audio.size, endpoint=False)
    x_new = np.linspace(0.0, duration, num=out_n, endpoint=False)
    out = np.interp(x_new, x_old, audio).astype(np.float32)
    return out
