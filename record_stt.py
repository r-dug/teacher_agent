#!/usr/bin/env python3
"""Speech-to-text / LLM / TTS voice assistant GUI."""

from __future__ import annotations

import argparse
import sys
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk

import numpy as np
import sounddevice as sd

from shared.audio import WakeWordDetector
from shared.constants import (
    KOKORO_VOICES,
    KOKORO_SAMPLE_RATE,
    DEFAULT_KOKORO_VOICE,
)
from shared.stt import FasterWhisperBackend, WhisperXBackend, detect_available_backends
from shared.ui import StderrInterceptor
from shared.voice_pipeline import VoicePipeline

SAMPLE_RATE = 16000
CHANNELS = 1
SILENCE_THRESHOLD = 0.01   # RMS below this counts as silence
WAKE_WORD_THRESHOLD = 0.5

DEFAULT_LLM_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = (
    "You are a helpful, conversational voice assistant. "
    "Keep responses concise and natural, as if speaking aloud. "
    "Write only plain prose — no markdown, bullet points, numbered lists, or symbols. "
    "Spell out numbers and abbreviations. "
    "Avoid em-dashes; use commas or periods instead."
)


class RecordApp:
    def __init__(
        self,
        root: tk.Tk,
        backend_name: str,
        model_size: str,
        kokoro_voice: str,
        llm_model: str,
        wake_word: str | None = None,
        silence_timeout: float = 5.0,
    ):
        self.root = root
        self.backend_name = backend_name
        self.model_size = model_size
        self.kokoro_voice = kokoro_voice
        self.llm_model = llm_model
        self.wake_word = wake_word
        self.silence_timeout = silence_timeout

        self.backend = None
        self.kokoro_pipeline = None
        self.wake_detector: WakeWordDetector | None = None
        self.pipeline: VoicePipeline | None = None

        self.messages: list = []
        self.last_audio: np.ndarray | None = None

        root.title("Voice Assistant")
        root.resizable(True, True)

        self.status_var = tk.StringVar(value="Loading models...")
        tk.Label(root, textvariable=self.status_var, font=("Helvetica", 12)).pack(pady=8)

        self.progress = ttk.Progressbar(root, mode="indeterminate", length=400)
        self.progress.pack(pady=(0, 8))
        self.progress.start(12)

        self.btn = tk.Button(
            root, text="Start Recording", font=("Helvetica", 14),
            bg="#4CAF50", fg="white", padx=20, pady=10,
            state=tk.DISABLED, command=self.toggle_recording,
        )
        self.btn.pack(pady=8)

        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=(0, 4))
        tk.Button(btn_frame, text="Copy", command=self.copy_to_clipboard).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_frame, text="Clear", command=self.clear_conversation).pack(side=tk.LEFT, padx=4)
        self.replay_btn = tk.Button(
            btn_frame, text="Say that again", state=tk.DISABLED, command=self.replay_last,
        )
        self.replay_btn.pack(side=tk.LEFT, padx=4)

        self.wake_var = tk.StringVar(value="")
        self.wake_label = tk.Label(root, textvariable=self.wake_var,
                                   font=("Helvetica", 10), fg="#888888")
        if wake_word:
            self.wake_label.pack(pady=(0, 2))

        tk.Label(root, text="Conversation:", font=("Helvetica", 11)).pack(anchor="w", padx=10)
        self.text_area = scrolledtext.ScrolledText(
            root, wrap=tk.WORD, font=("Helvetica", 11), height=16, state=tk.DISABLED,
        )
        self.text_area.tag_config("user_label", foreground="#1565C0", font=("Helvetica", 11, "bold"))
        self.text_area.tag_config("user_text", foreground="#1565C0")
        self.text_area.tag_config("assistant_label", foreground="#2E7D32", font=("Helvetica", 11, "bold"))
        self.text_area.tag_config("assistant_text", foreground="#2E7D32")
        self.text_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        threading.Thread(target=self._load_models, daemon=True).start()

    # ── model loading ──────────────────────────────────────────────────────────

    def _load_models(self):
        def on_progress(msg: str):
            self.root.after(0, self.status_var.set, msg)

        interceptor = StderrInterceptor(on_progress, sys.stderr)
        sys.stderr = interceptor
        try:
            if self.backend_name == "whisperx":
                self.backend = WhisperXBackend(self.model_size)
            else:
                self.backend = FasterWhisperBackend(self.model_size)

            self.root.after(0, self.status_var.set, "Loading Kokoro TTS...")
            from kokoro import KPipeline
            lang_code = KOKORO_VOICES.get(self.kokoro_voice, "a")
            self.kokoro_pipeline = KPipeline(lang_code=lang_code)

            if self.wake_word:
                self.root.after(0, self.status_var.set,
                                f"Loading wake word model ({self.wake_word})...")
                self.wake_detector = WakeWordDetector(
                    self.wake_word, self._on_wake_word, threshold=WAKE_WORD_THRESHOLD,
                )

            self.root.after(0, self._models_ready)
        except Exception as e:
            self.root.after(0, self._models_failed, str(e))
        finally:
            sys.stderr = interceptor._original

    def _models_failed(self, error: str):
        self.progress.stop()
        self.progress.pack_forget()
        self.status_var.set(f"Failed to load: {error}")

    def _models_ready(self):
        self.progress.stop()
        self.progress.pack_forget()
        self.status_var.set("Ready")
        self.btn.config(state=tk.NORMAL)

        self.pipeline = VoicePipeline(
            sample_rate=SAMPLE_RATE,
            stt_backend=self.backend,
            silence_threshold=SILENCE_THRESHOLD,
            silence_timeout=self.silence_timeout,
            channels=CHANNELS,
            wake_detector=self.wake_detector,
            on_transcription=self._on_transcription_cb,
            on_recording_stopped=self._on_recording_stopped_cb,
            on_error=lambda e: self.root.after(0, self._show_error, e),
        )
        self.pipeline.start_stream()

        if self.wake_detector:
            self.pipeline.arm_wake_detector()
            self._update_wake_indicator()

    def _on_wake_word(self):
        self.root.after(0, self._wake_triggered)

    def _wake_triggered(self):
        if self.pipeline and not self.pipeline.is_recording and self.btn["state"] == tk.NORMAL:
            self._start_recording()

    def _update_wake_indicator(self):
        if self.wake_detector is None:
            return
        if self.wake_detector.is_listening:
            self.wake_var.set(f'say "{self.wake_word}" to start recording')
        else:
            self.wake_var.set("")

    # ── VoicePipeline callbacks ────────────────────────────────────────────────

    def _on_recording_stopped_cb(self) -> None:
        self.root.after(0, self._on_recording_stopped_ui)

    def _on_recording_stopped_ui(self):
        self.btn.config(text="Start Recording", bg="#4CAF50", state=tk.DISABLED)
        self.status_var.set("Transcribing...")

    def _on_transcription_cb(self, text: str) -> None:
        self.root.after(0, self._on_transcription, text)

    # ── recording ──────────────────────────────────────────────────────────────

    def toggle_recording(self):
        if self.pipeline is None:
            return
        if not self.pipeline.is_recording:
            self._start_recording()
        else:
            self.pipeline.stop_recording()

    def _start_recording(self):
        if self.pipeline is None:
            return
        self.btn.config(text="Stop Recording", bg="#f44336")
        self.status_var.set("Recording...")
        if self.wake_detector:
            self._update_wake_indicator()
        self.pipeline.start_recording()

    # ── STT ────────────────────────────────────────────────────────────────────

    def _on_transcription(self, text: str):
        if not text:
            self.status_var.set("Ready")
            self.btn.config(state=tk.NORMAL)
            if self.wake_detector:
                self.pipeline.arm_wake_detector()
                self._update_wake_indicator()
            return
        self._append_text("You: ", "user_label")
        self._append_text(text + "\n\n", "user_text")
        self.messages.append({"role": "user", "content": text})
        self.status_var.set("Thinking...")
        threading.Thread(target=self._call_llm, daemon=True).start()

    # ── LLM ────────────────────────────────────────────────────────────────────

    def _call_llm(self):
        import anthropic
        client = anthropic.Anthropic()
        full_response = ""

        self.root.after(0, self._append_text, "Assistant: ", "assistant_label")
        try:
            with client.messages.stream(
                model=self.llm_model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=self.messages,
            ) as stream:
                for chunk in stream.text_stream:
                    full_response += chunk
                    self.root.after(0, self._append_text, chunk, "assistant_text")

            self.root.after(0, self._append_text, "\n\n", "assistant_text")
            self.messages.append({"role": "assistant", "content": full_response})
            self.root.after(0, self.status_var.set, "Speaking...")
            threading.Thread(
                target=self._synthesize_and_play, args=(full_response,), daemon=True
            ).start()
        except Exception as e:
            self.root.after(0, self._show_error, str(e))

    # ── TTS + playback ─────────────────────────────────────────────────────────

    def _synthesize_and_play(self, text: str):
        try:
            chunks = []
            for _, _, audio in self.kokoro_pipeline(text, voice=self.kokoro_voice):
                chunks.append(np.clip(audio.numpy(), -1.0, 1.0))

            if chunks:
                audio_data = np.concatenate(chunks).astype(np.float32)
                self.last_audio = audio_data
                self.pipeline.set_tts_playing(True)
                sd.play(audio_data, samplerate=KOKORO_SAMPLE_RATE)
                sd.wait()

            self.root.after(0, self._on_playback_done)
        except Exception as e:
            self.root.after(0, self._show_error, str(e))

    def _on_playback_done(self):
        self.status_var.set("Ready")
        self.btn.config(state=tk.NORMAL)
        if self.last_audio is not None:
            self.replay_btn.config(state=tk.NORMAL)
        if self.wake_detector:
            self.root.after(1000, self._arm_wake_detector)

    def _arm_wake_detector(self):
        if self.pipeline and not self.pipeline.is_recording:
            self.pipeline.set_tts_playing(False)
            self.pipeline.arm_wake_detector()
            self._update_wake_indicator()

    def replay_last(self):
        if self.last_audio is None:
            return
        self.btn.config(state=tk.DISABLED)
        self.replay_btn.config(state=tk.DISABLED)
        self.status_var.set("Speaking...")
        threading.Thread(target=self._replay, daemon=True).start()

    def _replay(self):
        try:
            sd.play(self.last_audio, samplerate=KOKORO_SAMPLE_RATE)
            sd.wait()
        finally:
            self.root.after(0, self._on_playback_done)

    # ── helpers ────────────────────────────────────────────────────────────────

    def _append_text(self, text: str, tag: str = ""):
        self.text_area.config(state=tk.NORMAL)
        self.text_area.insert(tk.END, text, tag)
        self.text_area.see(tk.END)
        self.text_area.config(state=tk.DISABLED)

    def _show_error(self, error: str):
        self.status_var.set(f"Error: {error}")
        self.btn.config(state=tk.NORMAL)
        if self.wake_detector:
            self.pipeline.arm_wake_detector()
            self._update_wake_indicator()

    def copy_to_clipboard(self):
        text = self.text_area.get("1.0", tk.END).strip()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set("Copied.")
        self.root.after(1500, lambda: self.status_var.set("Ready"))

    def clear_conversation(self):
        self.messages.clear()
        self.text_area.config(state=tk.NORMAL)
        self.text_area.delete("1.0", tk.END)
        self.text_area.config(state=tk.DISABLED)


def main():
    available = detect_available_backends()

    if not available:
        print("No STT backend found. Install at least one:")
        print("  uv add faster-whisper   # recommended")
        print("  pip install whisperx    # optional, requires specific torch version")
        return

    parser = argparse.ArgumentParser(description="Voice assistant: STT → LLM → TTS")
    if len(available) > 1:
        parser.add_argument(
            "--backend", choices=available, default=available[0],
            help=f"STT backend (available: {', '.join(available)}). Default: {available[0]}",
        )
    parser.add_argument(
        "--model", default="large-v3", metavar="SIZE",
        help="Whisper model size: tiny, base, small, medium, large-v3 (default: large-v3)",
    )
    parser.add_argument(
        "--voice", default=DEFAULT_KOKORO_VOICE, choices=list(KOKORO_VOICES),
        help=f"Kokoro TTS voice (default: {DEFAULT_KOKORO_VOICE})",
    )
    parser.add_argument(
        "--llm-model", default=DEFAULT_LLM_MODEL, metavar="MODEL",
        help=f"Claude model for responses (default: {DEFAULT_LLM_MODEL})",
    )
    parser.add_argument(
        "--wake-word", default=None, metavar="WORD",
        help=(
            "Enable wake-word activation. Built-in words: "
            "hey_jarvis, hey_mycroft, alexa, timer, weather. "
            "Default: disabled (use the button instead)."
        ),
    )
    parser.add_argument(
        "--silence-timeout", type=float, default=5.0, metavar="SECS",
        help="Seconds of silence before recording stops automatically (default: 5.0)",
    )
    args = parser.parse_args()

    backend_name = getattr(args, "backend", available[0])
    print(f"STT backend: {backend_name} ({args.model}), voice: {args.voice}, LLM: {args.llm_model}")
    if args.wake_word:
        print(f"Wake word: {args.wake_word}, silence timeout: {args.silence_timeout}s")

    root = tk.Tk()
    root.geometry("680x560")
    RecordApp(root, backend_name, args.model, args.voice, args.llm_model,
              args.wake_word, args.silence_timeout)
    root.mainloop()


if __name__ == "__main__":
    main()
