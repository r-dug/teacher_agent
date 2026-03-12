"""Shared audio utilities: wake-word detection and recording helpers."""

from __future__ import annotations

import queue
import threading

import numpy as np

from .constants import WAKE_WORD_CHUNK


class WakeWordDetector:
    """Wake-word detector fed via feed(); buffers internally to 1280-sample chunks.

    No own stream — the caller keeps one shared InputStream at native blocksize
    so recording quality is unaffected.

    Args:
        wake_word: The openwakeword model key to detect (e.g. "hey_jarvis").
        callback: Zero-argument callable invoked on the detection thread when
            the wake word is heard.  Callers typically schedule a main-thread
            action via ``root.after(0, ...)``.
        threshold: Confidence threshold in [0, 1] above which detection fires.
    """

    def __init__(self, wake_word: str, callback, threshold: float = 0.5):
        from openwakeword.model import Model
        self._wake_word = wake_word
        self._callback = callback
        self.threshold = threshold
        self._model = Model()
        self._buf = np.array([], dtype=np.int16)
        self._audio_queue: queue.Queue = queue.Queue()
        self._listening = threading.Event()
        self._stop = threading.Event()
        self.current_score: float = 0.0
        self._thread = threading.Thread(target=self._predict_loop, daemon=True)
        self._thread.start()

    # ── public API ─────────────────────────────────────────────────────────────

    def feed(self, mono_float32: np.ndarray) -> None:
        """Accept arbitrary-length float32 mono audio; chunks to 1280 samples."""
        if not self._listening.is_set():
            return
        self._buf = np.concatenate([self._buf, (mono_float32 * 32767).astype(np.int16)])
        while len(self._buf) >= WAKE_WORD_CHUNK:
            self._audio_queue.put(self._buf[:WAKE_WORD_CHUNK].copy())
            self._buf = self._buf[WAKE_WORD_CHUNK:]

    def set_listening(self, active: bool) -> None:
        """Arm or disarm the detector.  Drains stale audio on both transitions."""
        if active:
            self._buf = np.array([], dtype=np.int16)
            self._drain_queue()
            # Clear the model's rolling prediction buffer so stale scores don't
            # immediately re-trigger on the first new chunk.
            try:
                self._model.prediction_buffer.clear()
            except AttributeError:
                pass
            self._listening.set()
        else:
            self._listening.clear()
            self._buf = np.array([], dtype=np.int16)
            self._drain_queue()

    @property
    def is_listening(self) -> bool:
        return self._listening.is_set()

    def shutdown(self) -> None:
        """Signal the prediction thread to stop."""
        self._stop.set()

    # ── internals ──────────────────────────────────────────────────────────────

    def _drain_queue(self) -> None:
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

    def _predict_loop(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self._audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            scores = self._model.predict(chunk)
            self.current_score = float(scores.get(self._wake_word, 0))
            if self.current_score >= self.threshold:
                self._listening.clear()
                self._callback()
