"""TTSPipeline — synthesis queue and audio-player threads for one teaching session."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

import numpy as np
import sounddevice as sd

from ..voice.config import KOKORO_SAMPLE_RATE
from .callbacks import TeachingCallbacks

log = logging.getLogger(__name__)


class TTSPipeline:
    """
    Manages text-to-speech synthesis and audio delivery for a TeachingAgent session.

    Internally runs two daemon threads per turn:
    - _tts_worker: reads text from tts_queue, synthesises audio by trying each
      provider in order; on failure advances permanently to the next provider
      for the remainder of the turn (avoids hammering a rate-limited endpoint).
    - _audio_player: reads audio from audio_queue, delivers it via
      callbacks.on_audio_chunk (backend streaming) or sd.play() (local playback).

    Thread safety: call start_turn() → queue_text() (many times) → finish() once
    per turn.  Do not call across turns concurrently.
    """

    def __init__(
        self,
        providers: list,
        callbacks: TeachingCallbacks,
        preprocess_fn: Callable[[str], str] | None = None,
        tts_voice: str = "",
    ) -> None:
        """
        Args:
            providers: Ordered list of TTS provider instances, each with a
                .synthesize(text, voice) method.  Tried in sequence; once a
                provider fails it is skipped for the rest of the turn.
            callbacks: TeachingCallbacks with on_chunk_ready, on_audio_chunk, etc.
            preprocess_fn: If provided, called on text before synthesis when the
                provider sets requires_preprocessing=True.
            tts_voice: Initial voice identifier passed to synthesize().
        """
        self._providers = list(providers)
        self._callbacks = callbacks
        self._preprocess_fn = preprocess_fn
        self.tts_voice = tts_voice

        # Per-turn state — initialised by start_turn()
        self._tts_queue: queue.Queue | None = None
        self._audio_queue: queue.Queue | None = None
        self._audio_chunks: list[np.ndarray] = []
        self._tts_thread: threading.Thread | None = None
        self._audio_thread: threading.Thread | None = None
        self._turn_idx: int = 0
        self._STOP: object = object()

    # ── public API ──────────────────────────────────────────────────────────────

    def start_turn(self, turn_idx: int) -> None:
        """Initialise fresh queues and launch worker threads for a new LLM turn."""
        self._turn_idx = turn_idx
        self._audio_chunks = []
        self._STOP = object()
        self._tts_queue = queue.Queue()
        self._audio_queue = queue.Queue()

        self._tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
        self._audio_thread = threading.Thread(target=self._audio_player, daemon=True)
        self._tts_thread.start()
        self._audio_thread.start()

    def queue_text(self, tag: str, text: str, chunk_idx: int) -> None:
        """Hand a synthesisable text segment to the pipeline.

        Fires on_chunk_ready immediately so the client knows a chunk is coming,
        then puts the text on the synthesis queue.
        """
        if self._callbacks.on_chunk_ready:
            self._callbacks.on_chunk_ready(tag, self._turn_idx, chunk_idx)
        if self._tts_queue is not None:
            self._tts_queue.put(text)

    def finish(self) -> list[np.ndarray]:
        """Signal end-of-turn, drain queues, and return accumulated audio chunks."""
        if self._tts_queue is not None:
            self._tts_queue.put(None)  # sentinel
        if self._audio_thread is not None:
            self._audio_thread.join()
        return list(self._audio_chunks)

    def shutdown(self) -> None:
        """Cancel in-flight work (used on error or cancellation).

        Puts the stop sentinel on the queues so worker threads exit promptly.
        """
        if self._tts_queue is not None:
            self._tts_queue.put(None)
        if self._audio_thread is not None:
            self._audio_thread.join(timeout=2.0)

    # ── internal workers ────────────────────────────────────────────────────────

    def _prepare_text(self, provider, text: str) -> str:
        if getattr(provider, "requires_preprocessing", False) and self._preprocess_fn:
            return self._preprocess_fn(text)
        return text

    def _tts_worker(self) -> None:
        assert self._tts_queue is not None
        assert self._audio_queue is not None

        # Permanently advances when the current provider fails, so we don't
        # keep hammering a rate-limited endpoint for every subsequent chunk.
        provider_idx = 0

        while True:
            text = self._tts_queue.get()
            if text is None:
                self._audio_queue.put(self._STOP)
                return

            result = None
            for idx in range(provider_idx, len(self._providers)):
                provider = self._providers[idx]
                try:
                    speakable = self._prepare_text(provider, text)
                    result = provider.synthesize(speakable, self.tts_voice)
                    break
                except Exception as exc:
                    provider_idx = idx + 1
                    has_next = provider_idx < len(self._providers)
                    msg = f"TTS provider failed ({exc}). " + (
                        "Switching to fallback for this turn." if has_next else "All TTS providers exhausted."
                    )
                    log.warning("[TTSPipeline] %s", msg)
                    if self._callbacks.on_error:
                        self._callbacks.on_error(msg)

            if result is None:
                self._audio_queue.put(np.zeros(0, dtype=np.float32))
                continue

            self._audio_queue.put(result.audio)
            # Update voice in case provider selected a different one
            self.tts_voice = getattr(result, "voice", self.tts_voice)
            self._emit_tts_done(
                getattr(result, "voice", self.tts_voice),
                getattr(result, "characters", len(text)),
                len(result.audio) / max(1, getattr(result, "sample_rate", KOKORO_SAMPLE_RATE)),
                getattr(result, "synthesis_ms", 0),
                float(getattr(result, "estimated_cost_usd", 0.0)),
            )

    def _audio_player(self) -> None:
        assert self._audio_queue is not None

        chunk_idx = 0
        while True:
            audio_data = self._audio_queue.get()
            if audio_data is self._STOP:
                return

            self._audio_chunks.append(audio_data)

            try:
                if self._callbacks.on_tts_playing and audio_data.size > 0:
                    self._callbacks.on_tts_playing(True)
                if self._callbacks.on_audio_chunk:
                    self._callbacks.on_audio_chunk(audio_data, self._turn_idx, chunk_idx)
                elif audio_data.size > 0:
                    sd.play(audio_data, samplerate=KOKORO_SAMPLE_RATE)
                    sd.wait()
            except Exception as e:
                if self._callbacks.on_error:
                    self._callbacks.on_error(str(e))
                return

            chunk_idx += 1

    def _emit_tts_done(
        self,
        voice: str,
        characters: int,
        audio_seconds: float,
        synthesis_ms: int,
        estimated_cost_usd: float,
    ) -> None:
        if not self._callbacks.on_tts_done:
            return
        try:
            self._callbacks.on_tts_done(
                voice, characters, audio_seconds, synthesis_ms, estimated_cost_usd
            )
        except TypeError:
            self._callbacks.on_tts_done(voice, characters, audio_seconds, synthesis_ms)
