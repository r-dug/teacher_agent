"""Voice recording pipeline: mic stream, silence detection, and STT transcription.

No tkinter dependency. Communicates results exclusively via callbacks.
"""

from __future__ import annotations

import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

from .audio import WakeWordDetector
from .constants import SILENCE_POLL_MS


class VoicePipeline:
    """
    Owns a single shared sd.InputStream.

    Responsibilities:
    - Feed audio to the WakeWordDetector when not suppressed by TTS.
    - Accumulate recording frames while recording is active.
    - Track input RMS for a UI level meter.
    - Detect silence (via a background thread) and stop recording automatically.
    - Transcribe recorded audio via an STT backend, then fire on_transcription.

    All callbacks are invoked from background threads.  GUI callers should wrap
    them with ``root.after(0, ...)`` to safely update Tkinter widgets.
    """

    def __init__(
        self,
        sample_rate: int,
        stt_backend,
        silence_threshold: float = 0.01,
        silence_timeout: float = 5.0,
        channels: int = 1,
        wake_detector: WakeWordDetector | None = None,
        on_transcription: Callable[[str], None] | None = None,
        on_recording_stopped: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_rms_update: Callable[[float], None] | None = None,
    ):
        self._sample_rate = sample_rate
        self._stt_backend = stt_backend
        self._silence_threshold = silence_threshold
        self._silence_timeout = silence_timeout
        self._channels = channels
        self.wake_detector = wake_detector

        self._on_transcription = on_transcription
        self._on_recording_stopped = on_recording_stopped
        self._on_error = on_error
        self._on_rms_update = on_rms_update

        # Mutable state (GIL-protected single-assignment reads/writes are safe)
        self._is_recording: bool = False
        self._audio_frames: list[np.ndarray] = []
        self._last_sound: float = 0.0
        self._mic_rms: float = 0.0
        self._tts_suppressed: bool = False
        self.voice_act_enabled: bool = True
        self._stt_language: str | None = None

        self._stream: sd.InputStream | None = None

        # Silence-monitor: one persistent daemon thread, gated by an Event
        self._recording_event = threading.Event()
        self._monitor_thread = threading.Thread(
            target=self._silence_monitor, daemon=True
        )
        self._monitor_thread.start()

    # ── stream lifecycle ──────────────────────────────────────────────────────

    def start_stream(self) -> None:
        """Open and start the sd.InputStream."""
        def _callback(indata, frames, t, status):
            mono = indata[:, 0]
            rms = float(np.sqrt(np.mean(mono ** 2)))
            self._mic_rms = rms
            if self._on_rms_update:
                self._on_rms_update(rms)
            if (
                self.wake_detector
                and not self._tts_suppressed
                and self.voice_act_enabled
            ):
                self.wake_detector.feed(mono)
            if self._is_recording:
                self._audio_frames.append(indata.copy())
                if rms > self._silence_threshold:
                    self._last_sound = time.monotonic()

        self._stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="float32",
            callback=_callback,
        )
        self._stream.start()

    def stop_stream(self) -> None:
        """Stop and close the stream."""
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    # ── recording control ─────────────────────────────────────────────────────

    def start_recording(self) -> None:
        """Begin accumulating frames and arm the silence-detection thread."""
        self._audio_frames = []
        self._last_sound = time.monotonic()
        self._is_recording = True
        if self.wake_detector:
            self.wake_detector.set_listening(False)
        # Wake the silence monitor
        self._recording_event.set()

    def stop_recording(self) -> None:
        """Stop accumulating frames and launch transcription in a daemon thread."""
        if not self._is_recording:
            return
        self._is_recording = False
        frames = self._audio_frames[:]
        self._audio_frames = []
        if self._on_recording_stopped:
            self._on_recording_stopped()
        threading.Thread(
            target=self._do_transcribe, args=(frames,), daemon=True
        ).start()

    def cancel_recording(self) -> None:
        """Discard accumulated frames without transcribing."""
        self._is_recording = False
        self._audio_frames = []

    # ── wake detector control ─────────────────────────────────────────────────

    def arm_wake_detector(self) -> None:
        """Enable wake-word listening (call after TTS has drained)."""
        if self.wake_detector and not self._is_recording:
            self.wake_detector.set_listening(True)

    def disarm_wake_detector(self) -> None:
        """Disable wake-word listening."""
        if self.wake_detector:
            self.wake_detector.set_listening(False)

    # ── configuration ─────────────────────────────────────────────────────────

    def set_tts_playing(self, playing: bool) -> None:
        """Suppress wake-word detection while TTS audio is active."""
        self._tts_suppressed = playing

    def set_silence_threshold(self, value: float) -> None:
        self._silence_threshold = value

    def set_silence_timeout(self, value: float) -> None:
        self._silence_timeout = value

    def set_stt_language(self, language: str | None) -> None:
        self._stt_language = language

    def set_stt_backend(self, backend) -> None:
        """Hot-swap the STT backend (e.g. after the user changes the model size)."""
        self._stt_backend = backend

    # ── properties ────────────────────────────────────────────────────────────

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def current_rms(self) -> float:
        return self._mic_rms

    @property
    def silence_threshold(self) -> float:
        return self._silence_threshold

    # ── internals ─────────────────────────────────────────────────────────────

    def _silence_monitor(self) -> None:
        """Daemon thread: poll time-since-last-sound; call stop_recording on timeout."""
        while True:
            self._recording_event.wait()
            self._recording_event.clear()
            while self._is_recording:
                time.sleep(SILENCE_POLL_MS / 1000)
                if (
                    self._is_recording
                    and time.monotonic() - self._last_sound >= self._silence_timeout
                ):
                    self.stop_recording()
                    break

    def _do_transcribe(self, frames: list[np.ndarray]) -> None:
        """Write WAV, run STT, fire on_transcription callback."""
        if not frames:
            if self._on_transcription:
                self._on_transcription("")
            return

        audio_data = np.concatenate(frames, axis=0)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            sf.write(tmp_path, audio_data, self._sample_rate)
            text = self._stt_backend.transcribe(
                tmp_path, language=self._stt_language
            ).strip()
            if self._on_transcription:
                self._on_transcription(text)
        except Exception as e:
            if self._on_error:
                self._on_error(str(e))
        finally:
            Path(tmp_path).unlink(missing_ok=True)
