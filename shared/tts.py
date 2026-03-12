"""Text-to-speech synthesis helpers (Kokoro and Piper)."""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np

from .constants import (
    KOKORO_SAMPLE_RATE,
    KOKORO_VOICES,
    PIPER_VOICES,
    DEFAULT_PIPER_VOICE,
)

PIPER_MODELS_DIR = Path.home() / ".local" / "share" / "piper-models"


# ── text chunking ─────────────────────────────────────────────────────────────

def split_chunks(text: str, max_chars: int = 500) -> list[str]:
    """Split *text* into chunks at paragraph / sentence boundaries."""
    chunks: list[str] = []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        if current_len + len(para) > max_chars and current:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
        current.append(para)
        current_len += len(para)

    if current:
        chunks.append(" ".join(current))

    return chunks


# ── Kokoro ────────────────────────────────────────────────────────────────────

def synthesize_kokoro(text: str, output_path: str, voice: str) -> None:
    """Synthesize *text* to *output_path* (WAV or MP3) using the Kokoro pipeline."""
    from kokoro import KPipeline

    output = Path(output_path)
    is_mp3 = output.suffix.lower() == ".mp3"
    wav_path = output.with_suffix(".wav") if is_mp3 else output

    lang_code = KOKORO_VOICES.get(voice, voice[0])
    pipeline = KPipeline(lang_code=lang_code)

    print(f"Synthesizing with Kokoro ({voice})...")
    print(f"Writing {wav_path}...")
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(KOKORO_SAMPLE_RATE)
        for i, (gs, _, audio) in enumerate(pipeline(text, voice=voice)):
            print(f"  [sentence {i + 1}] {str(gs)[:60]}...")
            wf.writeframes(
                (np.clip(audio.numpy(), -1.0, 1.0) * 32767).astype(np.int16).tobytes()
            )

    if is_mp3:
        _wav_to_mp3(wav_path, output)

    print(f"Done: {output}")


# ── Piper ─────────────────────────────────────────────────────────────────────

def synthesize_piper(text: str, output_path: str, model_name: str) -> None:
    """Synthesize *text* to *output_path* (WAV or MP3) using a Piper ONNX voice."""
    from piper.voice import PiperVoice  # pip install piper-tts

    PIPER_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    model_file = PIPER_MODELS_DIR / f"{model_name}.onnx"

    if not model_file.exists():
        print(f"Downloading Piper model {model_name} (one-time)...")
        download_piper_model(model_name, model_file)

    print(f"Loading Piper voice: {model_name}")
    voice = PiperVoice.load(str(model_file))

    output = Path(output_path)
    is_mp3 = output.suffix.lower() == ".mp3"
    wav_path = output.with_suffix(".wav") if is_mp3 else output

    chunks = split_chunks(text)
    total = len(chunks)
    print(f"Synthesizing {total} chunks...")
    print(f"Writing {wav_path}...")
    with wave.open(str(wav_path), "wb") as wf:
        for i, chunk in enumerate(chunks, 1):
            print(f"  [{i}/{total}] {chunk[:60].replace(chr(10), ' ')}...")
            voice.synthesize_wav(chunk, wf, set_wav_format=(i == 1))

    if is_mp3:
        _wav_to_mp3(wav_path, output)

    print(f"Done: {output}")


def download_piper_model(model_name: str, dest: Path) -> None:
    """Download a Piper ONNX model and its JSON config from Hugging Face."""
    base = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
    parts = model_name.split("-")
    lang_region = parts[0]                   # e.g. en_US
    lang = lang_region.split("_")[0]         # e.g. en
    quality = parts[-1]                      # e.g. medium
    voice_name = "-".join(parts[1:-1])       # e.g. lessac  (or jenny_dioco)
    subpath = f"{lang}/{lang_region}/{voice_name}/{quality}"

    for ext in ("onnx", "onnx.json"):
        url = f"{base}/{subpath}/{model_name}.{ext}"
        out = dest.parent / f"{model_name}.{ext}"
        print(f"  Downloading {url}")
        subprocess.run(["curl", "-L", "-o", str(out), url], check=True)


# ── helpers ───────────────────────────────────────────────────────────────────

def _wav_to_mp3(wav_path: Path, mp3_path: Path) -> None:
    print("Converting WAV → MP3 via ffmpeg...")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path), str(mp3_path)],
        check=True,
        capture_output=True,
    )
    wav_path.unlink()


def resolve_piper_model(voice: str, model_override: str | None = None) -> str:
    """Return the Piper model name for *voice*, respecting an explicit override."""
    if model_override:
        return model_override
    return PIPER_VOICES.get(voice, PIPER_VOICES[DEFAULT_PIPER_VOICE])
