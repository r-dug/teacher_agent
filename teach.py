#!/usr/bin/env python3
"""Agentic document teacher: decompose PDF → teach each section → verify understanding via Q&A."""

from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

import numpy as np
import sounddevice as sd

from shared.audio import WakeWordDetector
from shared.constants import (
    KOKORO_VOICES,
    KOKORO_SAMPLE_RATE,
    DEFAULT_KOKORO_VOICE,
)
from shared.lesson import Curriculum, LessonStore
from shared.phonetics import (
    ACCENT_PROFILES,
    DEFAULT_ACCENT,
    WHISPER_LANGUAGES,
    WHISPER_MODELS,
)
from shared.stt import FasterWhisperBackend, WhisperXBackend, detect_available_backends
from shared.teaching_agent import TeachingAgent, DEFAULT_LLM_MODEL
from shared.ui import StderrInterceptor
from shared.voice_pipeline import VoicePipeline

# ── constants ─────────────────────────────────────────────────────────────────

SAMPLE_RATE = 24000           # recording sample rate (matches Kokoro output rate)
SILENCE_THRESHOLD = 0.001     # ≈ −50 dB; lower than typical speech (−45 dB through OBS)
WAKE_WORD_THRESHOLD = 0.005


# ── main application ──────────────────────────────────────────────────────────

class TeachApp:
    def __init__(
        self,
        root: tk.Tk,
        backend_name: str,
        model_size: str,
        kokoro_voice: str,
        llm_model: str,
        wake_word: str | None = None,
        silence_timeout: float = 5.0,
        silence_threshold: float = SILENCE_THRESHOLD,
    ):
        self.root = root
        self.backend_name = backend_name
        self.model_size = model_size
        self.kokoro_voice = kokoro_voice
        self.llm_model = llm_model
        self.wake_word = wake_word
        self.silence_timeout = silence_timeout
        self.silence_threshold = silence_threshold

        self.stt_language: str | None = None  # None = auto-detect
        self.stt_backend = None
        self.kokoro_pipeline = None
        self.wake_detector: WakeWordDetector | None = None

        # Lazy-initialised shared modules (after models load)
        self.pipeline: VoicePipeline | None = None
        self.agent: TeachingAgent | None = None

        # Click-to-play: per-turn audio chunk registry (tag → (turn_idx, chunk_idx))
        self._chunk_tag_map: dict[str, tuple[int, int]] = {}
        self._current_chunk_start: str = "1.0"
        self._click_job: str | None = None

        # Lesson state
        self.curriculum: Curriculum | None = None
        self.messages: list[dict] = []
        self.pdf_path: str | None = None
        self.completed: bool = False
        self.agent_instructions: str | None = None
        self.personas: dict[str, dict] = LessonStore.load_personas()

        # UI state
        self._tts_playing = False
        self._voice_act_enabled: bool = True
        self._wake_threshold: float = WAKE_WORD_THRESHOLD
        self.show_ipa: bool = True
        self._display_log: list[tuple[str, str]] = []
        self.accent: str = DEFAULT_ACCENT

        self._build_ui(root)
        threading.Thread(target=self._load_models, daemon=True).start()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self, root: tk.Tk):
        root.title("Document Teacher")
        root.resizable(True, True)

        self.status_var = tk.StringVar(value="Loading models...")
        tk.Label(root, textvariable=self.status_var, font=("Helvetica", 12)).pack(pady=(10, 4))

        self.model_progress = ttk.Progressbar(root, mode="indeterminate", length=420)
        self.model_progress.pack(pady=(0, 6))
        self.model_progress.start(12)

        # Section info bar (hidden until teaching starts)
        self.section_frame = tk.Frame(root)
        self.section_var = tk.StringVar(value="")
        tk.Label(self.section_frame, textvariable=self.section_var,
                 font=("Helvetica", 11, "italic")).pack(anchor="w")
        self.section_progress = ttk.Progressbar(
            self.section_frame, mode="determinate", length=420, maximum=1.0
        )
        self.section_progress.pack(fill=tk.X, pady=(2, 0))

        # Load / resume buttons
        pdf_btn_frame = tk.Frame(root)
        pdf_btn_frame.pack(pady=8)
        self.load_btn = tk.Button(
            pdf_btn_frame, text="Load PDF to Teach", font=("Helvetica", 13),
            bg="#7B1FA2", fg="white", padx=16, pady=8,
            state=tk.DISABLED, command=self._pick_pdf,
        )
        self.load_btn.pack(side=tk.LEFT, padx=4)
        self.resume_btn = tk.Button(
            pdf_btn_frame, text="Resume Lesson", font=("Helvetica", 13),
            bg="#455A64", fg="white", padx=16, pady=8,
            state=tk.DISABLED, command=self._pick_lesson,
        )
        self.resume_btn.pack(side=tk.LEFT, padx=4)

        # Teaching style customization
        customize_frame = tk.Frame(root)
        customize_frame.pack(pady=(0, 4), padx=10, fill=tk.X)

        persona_row = tk.Frame(customize_frame)
        persona_row.pack(fill=tk.X, pady=(0, 2))
        tk.Label(persona_row, text="Persona:", font=("Helvetica", 10)).pack(side=tk.LEFT)
        self.persona_var = tk.StringVar()
        self.persona_combo = ttk.Combobox(
            persona_row, textvariable=self.persona_var, state="readonly", width=28,
            values=list(self.personas.keys()),
        )
        self.persona_combo.pack(side=tk.LEFT, padx=(4, 0))
        self.persona_combo.bind("<<ComboboxSelected>>", self._on_persona_selected)
        tk.Button(persona_row, text="Save", font=("Helvetica", 10),
                  command=self._save_persona).pack(side=tk.LEFT, padx=(4, 0))
        tk.Button(persona_row, text="Delete", font=("Helvetica", 10),
                  command=self._delete_persona).pack(side=tk.LEFT, padx=(2, 0))

        tk.Label(customize_frame, text="Teaching style (optional):",
                 font=("Helvetica", 10)).pack(anchor="w")
        input_row = tk.Frame(customize_frame)
        input_row.pack(fill=tk.X)
        self.style_input = tk.Text(input_row, height=2, font=("Helvetica", 10), wrap=tk.WORD)
        self.style_input.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.build_btn = tk.Button(
            input_row, text="Build", font=("Helvetica", 10),
            command=self._generate_instructions,
        )
        self.build_btn.pack(side=tk.LEFT, padx=(4, 0))
        self.instructions_var = tk.StringVar(value="")
        tk.Label(customize_frame, textvariable=self.instructions_var,
                 font=("Helvetica", 9), fg="#666666").pack(anchor="w")

        # Record + cancel buttons
        record_row = tk.Frame(root)
        self.record_btn = tk.Button(
            record_row, text="Start Recording", font=("Helvetica", 14),
            bg="#4CAF50", fg="white", padx=20, pady=10,
            state=tk.DISABLED, command=self.toggle_recording,
        )
        self.record_btn.pack(side=tk.LEFT)
        self.cancel_btn = tk.Button(
            record_row, text="Cancel", font=("Helvetica", 14),
            bg="#FF9800", fg="white", padx=12, pady=10,
            command=self._cancel_recording,
        )
        record_row.pack(pady=(0, 6))

        # Secondary buttons + voice selector
        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=(0, 4))
        tk.Button(btn_frame, text="Copy", command=self.copy_text).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_frame, text="Clear", command=self.clear_conversation).pack(side=tk.LEFT, padx=4)
        self.replay_btn = tk.Button(
            btn_frame, text="Say that again", state=tk.DISABLED, command=self.replay_last,
        )
        self.replay_btn.pack(side=tk.LEFT, padx=4)
        self.ipa_btn = tk.Button(
            btn_frame, text="IPA: ON", font=("Helvetica", 10),
            command=self._toggle_ipa, padx=6,
        )
        self.ipa_btn.pack(side=tk.LEFT, padx=4)
        self.voice_act_btn = tk.Button(
            btn_frame, text="Voice Act: ON", font=("Helvetica", 10),
            command=self._toggle_voice_activation, padx=6,
        )
        self.voice_act_btn.pack(side=tk.LEFT, padx=4)
        tk.Label(btn_frame, text="Voice:", font=("Helvetica", 10)).pack(side=tk.LEFT, padx=(12, 2))
        self.voice_var = tk.StringVar(value=self.kokoro_voice)
        voice_combo = ttk.Combobox(
            btn_frame, textvariable=self.voice_var,
            values=list(KOKORO_VOICES.keys()), state="readonly", width=12,
        )
        voice_combo.pack(side=tk.LEFT)
        voice_combo.bind("<<ComboboxSelected>>", self._on_voice_selected)

        # STT settings row
        stt_frame = tk.Frame(root)
        stt_frame.pack(pady=(0, 4))
        tk.Label(stt_frame, text="STT model:", font=("Helvetica", 10)).pack(side=tk.LEFT, padx=(0, 2))
        self.stt_model_var = tk.StringVar(value=self.model_size)
        stt_model_combo = ttk.Combobox(
            stt_frame, textvariable=self.stt_model_var,
            values=WHISPER_MODELS, state="readonly", width=14,
        )
        stt_model_combo.pack(side=tk.LEFT)
        stt_model_combo.bind("<<ComboboxSelected>>", self._on_stt_model_selected)
        tk.Label(stt_frame, text="Language:", font=("Helvetica", 10)).pack(side=tk.LEFT, padx=(12, 2))
        self.stt_lang_var = tk.StringVar(value="Auto-detect")
        stt_lang_combo = ttk.Combobox(
            stt_frame, textvariable=self.stt_lang_var,
            values=list(WHISPER_LANGUAGES.keys()), state="readonly", width=14,
        )
        stt_lang_combo.pack(side=tk.LEFT)
        stt_lang_combo.bind("<<ComboboxSelected>>", self._on_stt_lang_selected)
        tk.Label(stt_frame, text="Accent:", font=("Helvetica", 10)).pack(side=tk.LEFT, padx=(12, 2))
        self.accent_var = tk.StringVar(value=DEFAULT_ACCENT)
        accent_combo = ttk.Combobox(
            stt_frame, textvariable=self.accent_var,
            values=list(ACCENT_PROFILES.keys()), state="readonly", width=18,
        )
        accent_combo.pack(side=tk.LEFT)
        accent_combo.bind("<<ComboboxSelected>>", self._on_accent_selected)

        # Silence threshold slider + live mic meter
        silence_row = tk.Frame(root)
        silence_row.pack(pady=(0, 2))
        tk.Label(silence_row, text="Silence threshold:", font=("Helvetica", 10)).pack(side=tk.LEFT, padx=(0, 4))
        _clamped = max(0.0005, min(self.silence_threshold, 0.03))
        self.silence_threshold = _clamped
        self._sensitivity_var = tk.DoubleVar(value=_clamped)
        self._sensitivity_label_var = tk.StringVar(value=f"{_clamped:.4f}")
        sensitivity_slider = tk.Scale(
            silence_row, variable=self._sensitivity_var,
            from_=0.0005, to=0.03, resolution=0.0001, orient=tk.HORIZONTAL,
            length=160, showvalue=False,
            command=self._on_sensitivity_changed,
        )
        sensitivity_slider.pack(side=tk.LEFT)
        tk.Label(silence_row, textvariable=self._sensitivity_label_var,
                 font=("Helvetica", 10), width=6).pack(side=tk.LEFT, padx=(4, 0))

        meter_row = tk.Frame(root)
        meter_row.pack(pady=(0, 2))
        tk.Label(meter_row, text="Input level:", font=("Helvetica", 10),
                 fg="#888888").pack(side=tk.LEFT, padx=(0, 4))
        self._meter_canvas = tk.Canvas(
            meter_row, width=160, height=12, bg="#222222", highlightthickness=0,
        )
        self._meter_canvas.pack(side=tk.LEFT)

        # Wake word indicator
        self.wake_var = tk.StringVar(value="")
        self.wake_label = tk.Label(root, textvariable=self.wake_var,
                                   font=("Helvetica", 10), fg="#888888")
        if self.wake_word:
            self.wake_label.pack(pady=(0, 2))

        tk.Label(root, text="Lesson:", font=("Helvetica", 11)).pack(anchor="w", padx=10)
        self.text_area = scrolledtext.ScrolledText(
            root, wrap=tk.WORD, font=("Helvetica", 11), height=16, state=tk.DISABLED,
        )
        self.text_area.tag_config("user_label",   foreground="#1565C0", font=("Helvetica", 11, "bold"))
        self.text_area.tag_config("user_text",     foreground="#1565C0")
        self.text_area.tag_config("teacher_label", foreground="#2E7D32", font=("Helvetica", 11, "bold"))
        self.text_area.tag_config("teacher_text",  foreground="#2E7D32")
        self.text_area.tag_config("system_text",   foreground="#888888", font=("Helvetica", 10, "italic"))
        self.text_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.text_area.bind("<Button-1>", self._on_text_click)
        self.text_area.bind("<Double-Button-1>", self._on_text_double_click)

    # ── model loading ─────────────────────────────────────────────────────────

    def _load_models(self):
        def on_progress(msg: str):
            self.root.after(0, self.status_var.set, msg)

        interceptor = StderrInterceptor(on_progress, sys.stderr)
        sys.stderr = interceptor
        try:
            if self.backend_name == "whisperx":
                self.stt_backend = WhisperXBackend(self.model_size)
            else:
                self.stt_backend = FasterWhisperBackend(self.model_size)

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
            self.root.after(0, self.status_var.set, f"Failed to load models: {e}")
        finally:
            sys.stderr = interceptor._original

    def _models_ready(self):
        self.model_progress.stop()
        self.model_progress.pack_forget()
        self.status_var.set("Ready — load a PDF to begin.")
        self.load_btn.config(state=tk.NORMAL)
        if any(LessonStore.list_saved()):
            self.resume_btn.config(state=tk.NORMAL)

        # Build the shared pipeline and agent now that models are ready.
        self.pipeline = VoicePipeline(
            sample_rate=SAMPLE_RATE,
            stt_backend=self.stt_backend,
            silence_threshold=self.silence_threshold,
            silence_timeout=self.silence_timeout,
            channels=1,
            wake_detector=self.wake_detector,
            on_transcription=self._on_transcription_cb,
            on_recording_stopped=self._on_recording_stopped_cb,
            on_error=lambda e: self.root.after(0, self._show_error, e),
            on_rms_update=self._on_rms_update_cb,
        )
        self.pipeline.voice_act_enabled = self._voice_act_enabled
        self.pipeline.start_stream()

        self.agent = TeachingAgent(
            llm_model=self.llm_model,
            kokoro_pipeline=self.kokoro_pipeline,
            kokoro_voice=self.kokoro_voice,
            accent=self.accent,
            on_status=lambda s: self.root.after(0, self.status_var.set, s),
            on_turn_start=self._on_agent_turn_start,
            on_text_chunk=self._on_agent_text_chunk,
            on_chunk_ready=self._on_agent_chunk_ready,
            on_response_end=self._on_agent_response_end,
            on_show_slide=self._on_agent_show_slide,
            on_open_sketchpad=self._on_agent_open_sketchpad,
            on_section_advanced=self._on_agent_section_advanced,
            on_curriculum_complete=self._on_agent_curriculum_complete,
            on_turn_complete=self._on_agent_turn_complete,
            on_tts_playing=self._on_tts_playing_cb,
            on_error=lambda e: self.root.after(0, self._show_error, e),
        )

        if self.wake_detector:
            self.pipeline.arm_wake_detector()
            self._update_wake_indicator()

        self._update_meter()

    def _update_meter(self):
        _meter_max = 0.02
        rms = self.pipeline.current_rms if self.pipeline else 0.0
        level = min(rms / _meter_max, 1.0)
        marker = min(self.silence_threshold / _meter_max, 1.0)
        w, h = 160, 12
        c = self._meter_canvas
        c.delete("all")
        bar_w = int(level * w)
        if bar_w > 0:
            color = "#4CAF50" if level < marker else "#FF9800"
            c.create_rectangle(0, 0, bar_w, h, fill=color, outline="")
        marker_x = max(1, int(marker * w))
        c.create_line(marker_x, 0, marker_x, h, fill="white", width=2)
        self.root.after(50, self._update_meter)

    def _on_wake_word(self):
        self.root.after(0, self._wake_triggered)

    def _wake_triggered(self):
        if not self.pipeline.is_recording and self.record_btn["state"] == tk.NORMAL:
            self._start_recording()

    def _update_wake_indicator(self):
        if self.wake_detector is None:
            return
        if self.wake_detector.is_listening:
            self.wake_var.set(f'say "{self.wake_word}" to start recording')
        else:
            self.wake_var.set("")

    # ── VoicePipeline callbacks (all called from background thread) ───────────

    def _on_rms_update_cb(self, rms: float) -> None:
        pass  # pipeline.current_rms is read directly by _update_meter

    def _on_recording_stopped_cb(self) -> None:
        self.root.after(0, self._on_recording_stopped_ui)

    def _on_recording_stopped_ui(self):
        self.cancel_btn.pack_forget()
        self.record_btn.config(text="Start Recording", bg="#4CAF50", state=tk.DISABLED)
        self.replay_btn.config(state=tk.DISABLED)
        self.status_var.set("Transcribing...")

    def _on_transcription_cb(self, text: str) -> None:
        self.root.after(0, self._on_transcription, text)

    # ── TeachingAgent callbacks (all called from background thread) ───────────

    def _on_agent_turn_start(self) -> None:
        self.root.after(0, self._append, "teacher_label", "Teacher: ")
        self.root.after(0, self._init_chunk_tracking)

    def _on_agent_text_chunk(self, chunk: str) -> None:
        self.root.after(0, self._append, "teacher_text", chunk)

    def _on_agent_chunk_ready(self, tag: str, turn_idx: int, chunk_idx: int) -> None:
        self._chunk_tag_map[tag] = (turn_idx, chunk_idx)
        self.root.after(0, self._finalize_chunk, tag)

    def _on_agent_response_end(self) -> None:
        self.root.after(0, self._append, "teacher_text", "\n\n")

    def _on_agent_show_slide(self, page_number: int, caption: str) -> None:
        self.root.after(0, self._open_slide_popup, page_number, caption)

    def _on_agent_open_sketchpad(
        self, prompt: str, result_holder: list, done_event: threading.Event
    ) -> None:
        self.root.after(0, self._open_sketchpad, prompt, result_holder, done_event)

    def _on_agent_section_advanced(self, curriculum: Curriculum) -> None:
        self._save_lesson()
        self.root.after(0, self._update_section_ui)
        self.root.after(
            0, self._append, "system_text",
            f"\n-- Section {curriculum.idx + 1}: {curriculum.current['title']} --\n\n",
        )

    def _on_agent_curriculum_complete(self) -> None:
        self._save_lesson()
        self.root.after(0, self._complete)

    def _on_agent_turn_complete(self, last_audio) -> None:
        self._save_lesson()
        self.root.after(0, self._enable_recording)

    def _on_tts_playing_cb(self, playing: bool) -> None:
        self._tts_playing = playing
        if self.pipeline:
            self.pipeline.set_tts_playing(playing)

    # ── personas ──────────────────────────────────────────────────────────────

    def _on_persona_selected(self, _event=None):
        name = self.persona_var.get()
        p = self.personas.get(name)
        if not p:
            return
        self.style_input.delete("1.0", tk.END)
        self.style_input.insert("1.0", p.get("description", ""))
        self.agent_instructions = p.get("instructions")
        if self.agent_instructions:
            self.instructions_var.set(f'Persona "{name}" active.')
        else:
            self.instructions_var.set("")

    def _save_persona(self):
        description = self.style_input.get("1.0", tk.END).strip()
        if not description and not self.agent_instructions:
            return
        default = self.persona_var.get() or ""
        name = simpledialog.askstring("Save persona", "Persona name:", initialvalue=default)
        if not name:
            return
        self.personas[name] = {
            "description": description,
            "instructions": self.agent_instructions or "",
        }
        LessonStore.save_personas(self.personas)
        self.persona_combo["values"] = list(self.personas.keys())
        self.persona_var.set(name)
        self.instructions_var.set(f'Persona "{name}" saved.')

    def _delete_persona(self):
        name = self.persona_var.get()
        if not name or name not in self.personas:
            return
        if not messagebox.askyesno("Delete persona", f'Delete "{name}"?'):
            return
        del self.personas[name]
        LessonStore.save_personas(self.personas)
        self.persona_combo["values"] = list(self.personas.keys())
        self.persona_var.set("")
        self.instructions_var.set("")

    # ── teaching style generation ─────────────────────────────────────────────

    def _generate_instructions(self):
        description = self.style_input.get("1.0", tk.END).strip()
        if not description:
            return
        self.build_btn.config(state=tk.DISABLED)
        self.instructions_var.set("Building instructions...")

        def _run():
            try:
                result = self.agent.generate_instructions(description)
                self.agent_instructions = result
                self.root.after(0, self.instructions_var.set,
                                "Instructions built — click Save to keep as a persona.")
            except Exception as e:
                self.root.after(0, self.instructions_var.set, f"Error: {e}")
            finally:
                self.root.after(0, self.build_btn.config, {"state": tk.NORMAL})

        threading.Thread(target=_run, daemon=True).start()

    # ── PDF loading & decomposition ───────────────────────────────────────────

    def _save_lesson(self):
        if self.curriculum is None or self.pdf_path is None or self.agent is None:
            return
        LessonStore.save(
            curriculum=self.curriculum,
            pdf_path=self.pdf_path,
            messages=self.messages,
            completed=self.completed,
            display_log=self._display_log,
            chunk_tag_map=self._chunk_tag_map,
            audio_turns=self.agent.audio_turns,
        )

    def _pick_pdf(self):
        path = filedialog.askopenfilename(
            title="Select PDF to teach",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if not path:
            return
        self.pdf_path = path
        lesson_file = LessonStore.lesson_path(path)
        if lesson_file.exists():
            saved = json.loads(lesson_file.read_text(encoding="utf-8"))
            sec_idx = saved["curriculum"]["idx"] + 1
            n_exchanges = sum(1 for m in saved.get("messages", []) if m["role"] == "user")
            detail = f"section {sec_idx}"
            if n_exchanges > 1:
                detail += f", question {n_exchanges}"
            if messagebox.askyesno(
                "Resume lesson?",
                f"A saved lesson exists for this PDF.\nResume from {detail}?",
            ):
                self._start_from_lesson(lesson_file)
                return
        self.load_btn.config(state=tk.DISABLED)
        self.resume_btn.config(state=tk.DISABLED)
        self.status_var.set("Decomposing document...")
        self.model_progress.pack(pady=(0, 6))
        self.model_progress.start(12)
        threading.Thread(target=self._decompose, args=(path,), daemon=True).start()

    def _pick_lesson(self):
        from shared.lesson import LESSONS_DIR
        path = filedialog.askopenfilename(
            title="Select a saved lesson",
            initialdir=str(LESSONS_DIR) if LESSONS_DIR.exists() else ".",
            filetypes=[("Lesson files", "*.lesson.json"), ("All files", "*.*")],
        )
        if not path:
            return
        self._start_from_lesson(Path(path))

    def _start_from_lesson(self, lesson_file: Path):
        try:
            snap = LessonStore.load(lesson_file)
        except Exception as e:
            messagebox.showerror("Load error", str(e))
            return
        self.pdf_path = snap.pdf_path
        self.curriculum = snap.curriculum
        self.messages = snap.messages
        self.completed = snap.completed
        self._display_log = list(snap.display_log)
        self._chunk_tag_map = snap.chunk_tag_map
        if self.agent:
            self.agent._audio_turns = snap.audio_turns
        self.completed = False
        self._decomposition_ready(snap.curriculum, resumed=True)

    def _decompose(self, path: str):
        try:
            def on_progress(msg: str):
                self.root.after(0, self.status_var.set, msg)

            curriculum = self.agent.decompose_pdf(path, on_progress=on_progress)
            self.root.after(0, self._decomposition_ready, curriculum)
        except Exception as e:
            self.root.after(0, self.status_var.set, f"Decomposition failed: {e}")
            self.root.after(0, self.load_btn.config, {"state": tk.NORMAL})
            self.root.after(0, self.model_progress.stop)
            self.root.after(0, self.model_progress.pack_forget)

    def _decomposition_ready(self, curriculum: Curriculum, resumed: bool = False):
        self.curriculum = curriculum
        if not resumed:
            self.messages = []
        self.model_progress.stop()
        self.model_progress.pack_forget()
        self._save_lesson()

        n = len(curriculum.sections)
        prefix = "Resuming" if resumed else "Curriculum"
        self._append("system_text",
                     f'{prefix}: "{curriculum.title}" — {n} sections.\n'
                     + "  " + " | ".join(f"{i+1}. {s['title']}" for i, s in enumerate(curriculum.sections))
                     + "\n\n")
        if resumed:
            self._append("system_text",
                         f"Resuming at section {curriculum.idx + 1}: {curriculum.current['title']}\n\n")

        self.section_frame.pack(pady=(0, 6), padx=10, fill=tk.X)
        self._update_section_ui()
        self.load_btn.config(state=tk.NORMAL)
        self.resume_btn.config(state=tk.NORMAL)

        if resumed:
            self._render_history_to_ui()
            last_teacher_text: str | None = None
            for msg in reversed(self.messages):
                if msg.get("role") == "assistant":
                    for block in (msg.get("content") or []):
                        if isinstance(block, dict) and block.get("type") == "text":
                            last_teacher_text = block["text"]
                            break
                if last_teacher_text:
                    break
            if last_teacher_text and self.kokoro_pipeline:
                self.status_var.set("Resuming...")
                self._speak_text(last_teacher_text)
            else:
                self._enable_recording()
        else:
            self.status_var.set("Starting lesson...")
            threading.Thread(
                target=self.agent.run_turn,
                args=(self.curriculum, self.messages, self.agent_instructions),
                daemon=True,
            ).start()

    def _update_section_ui(self):
        c = self.curriculum
        self.section_var.set(
            f"Section {c.idx + 1} / {len(c.sections)}: {c.current['title']}"
        )
        self.section_progress["value"] = c.idx / len(c.sections)

    # ── recording ─────────────────────────────────────────────────────────────

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
        self.record_btn.config(text="Stop Recording", bg="#f44336")
        self.cancel_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.status_var.set("Recording...")
        if self.wake_detector:
            self._update_wake_indicator()
        self.pipeline.start_recording()

    def _cancel_recording(self):
        if self.pipeline is None or not self.pipeline.is_recording:
            return
        self.pipeline.cancel_recording()
        self.cancel_btn.pack_forget()
        self.record_btn.config(text="Start Recording", bg="#4CAF50")
        self.status_var.set("Recording cancelled.")
        self._enable_recording()

    # ── STT ───────────────────────────────────────────────────────────────────

    def _on_transcription(self, text: str):
        if not text:
            self._enable_recording()
            return
        self._append("user_label", "You: ")
        self._append("user_text", text + "\n\n")
        self.messages.append({"role": "user", "content": text})
        self.status_var.set("Thinking...")
        threading.Thread(
            target=self.agent.run_turn,
            args=(self.curriculum, self.messages, self.agent_instructions),
            daemon=True,
        ).start()

    # ── GUI callbacks from TeachingAgent ─────────────────────────────────────

    def _open_slide_popup(self, page_number: int, caption: str):
        if not self.pdf_path:
            return
        try:
            import fitz
            doc = fitz.open(self.pdf_path)
            if page_number < 1 or page_number > len(doc):
                return
            page = doc[page_number - 1]
            scale = 700 / page.rect.width
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
            png_bytes = pix.tobytes("png")
            doc.close()
        except Exception as e:
            self._show_error(f"Slide error: {e}")
            return

        popup = tk.Toplevel(self.root)
        popup.title(f"Page {page_number}")
        popup.attributes("-topmost", True)
        popup.resizable(True, True)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(png_bytes)
            tmp_path = f.name

        img = tk.PhotoImage(file=tmp_path)
        Path(tmp_path).unlink(missing_ok=True)

        img_label = tk.Label(popup, image=img)
        img_label.image = img  # prevent GC
        img_label.pack(padx=8, pady=(8, 4))

        if caption:
            tk.Label(popup, text=caption, font=("Helvetica", 10),
                     wraplength=680, justify=tk.LEFT).pack(padx=8, pady=(0, 4))

        tk.Button(popup, text="Close", command=popup.destroy).pack(pady=(0, 8))

    def _open_sketchpad(self, prompt: str, result_holder: list, done_event: threading.Event):
        from PIL import Image, ImageDraw

        CANVAS_W, CANVAS_H = 520, 420
        BG = "white"

        pil_img = Image.new("RGB", (CANVAS_W, CANVAS_H), BG)
        pil_draw = ImageDraw.Draw(pil_img)

        popup = tk.Toplevel(self.root)
        popup.title("Sketchpad")
        popup.attributes("-topmost", True)
        popup.resizable(False, False)
        popup.protocol("WM_DELETE_WINDOW", lambda: None)

        tk.Label(popup, text=prompt, font=("Helvetica", 13, "bold"),
                 wraplength=500, justify=tk.CENTER).pack(padx=8, pady=(10, 4))

        canvas = tk.Canvas(popup, width=CANVAS_W, height=CANVAS_H, bg=BG,
                           cursor="pencil", relief=tk.SUNKEN, bd=1)
        canvas.pack(padx=8, pady=4)

        last: list = [None]

        def on_press(event):
            last[0] = (event.x, event.y)

        def on_drag(event):
            if last[0] is None:
                return
            x0, y0 = last[0]
            x1, y1 = event.x, event.y
            canvas.create_line(x0, y0, x1, y1, width=3, fill="black",
                               capstyle=tk.ROUND, joinstyle=tk.ROUND, smooth=True)
            pil_draw.line([x0, y0, x1, y1], fill="black", width=3)
            last[0] = (x1, y1)

        def on_release(_event):
            last[0] = None

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)

        def clear():
            canvas.delete("all")
            pil_draw.rectangle([0, 0, CANVAS_W, CANVAS_H], fill=BG)

        def _finish():
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            result_holder[0] = base64.standard_b64encode(buf.getvalue()).decode()
            popup.destroy()
            done_event.set()

        btn_row = tk.Frame(popup)
        btn_row.pack(pady=(4, 10))
        tk.Button(btn_row, text="Clear", command=clear,
                  padx=12, pady=6).pack(side=tk.LEFT, padx=6)
        tk.Button(btn_row, text="Cancel", command=_finish,
                  padx=12, pady=6).pack(side=tk.LEFT, padx=6)
        tk.Button(btn_row, text="Submit Drawing", command=_finish,
                  bg="#4CAF50", fg="white", padx=12, pady=6).pack(side=tk.LEFT, padx=6)

    def _complete(self):
        self.completed = True
        self._save_lesson()
        self.section_progress["value"] = 1.0
        self.section_var.set("Curriculum complete!")
        self.status_var.set("Lesson finished.")
        self._append("system_text", "\n-- Curriculum complete! --\n")
        self.record_btn.config(state=tk.DISABLED)

    # ── TTS + playback ────────────────────────────────────────────────────────

    def replay_last(self):
        if self.agent is None or self.agent.last_audio is None:
            return
        self.record_btn.config(state=tk.DISABLED)
        self.replay_btn.config(state=tk.DISABLED)
        self.status_var.set("Speaking...")
        audio = self.agent.last_audio

        def _run():
            try:
                self.agent.play_audio(audio, stop_first=True)
            finally:
                self.root.after(0, self._enable_recording)

        threading.Thread(target=_run, daemon=True).start()

    def _speak_text(self, text: str):
        """Synthesise and play a single text block, then enable recording."""
        def _run():
            try:
                spoken = self.agent.prepare_for_tts(text)
                chunks = [
                    np.clip(audio.numpy(), -1.0, 1.0)
                    for _, _, audio in self.kokoro_pipeline(spoken, voice=self.kokoro_voice)
                ]
                if chunks:
                    audio_data = np.concatenate(chunks).astype(np.float32)
                    self.agent.last_audio = audio_data
                    self.agent.play_audio(audio_data)
            except Exception as e:
                self.root.after(0, self._show_error, str(e))
                return
            self.root.after(0, self._enable_recording)

        threading.Thread(target=_run, daemon=True).start()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _enable_recording(self):
        if self.wake_word:
            self.status_var.set(f'Your turn — say "{self.wake_word}" or press Record.')
        else:
            self.status_var.set("Your turn — press Record to respond.")
        self.record_btn.config(state=tk.NORMAL)
        if self.agent and self.agent.last_audio is not None:
            self.replay_btn.config(state=tk.NORMAL)
        if self.wake_detector:
            self.root.after(2000, self._arm_wake_detector)

    def _arm_wake_detector(self):
        if self.pipeline and not self.pipeline.is_recording:
            self._tts_playing = False
            if self.pipeline:
                self.pipeline.set_tts_playing(False)
            self.pipeline.arm_wake_detector()
            self._update_wake_indicator()

    _IPA_RE = re.compile(r'\[([^\]]+)\]\(/[^/]+/\)')

    @staticmethod
    def _strip_ipa(text: str) -> str:
        return TeachApp._IPA_RE.sub(r'\1', text)

    def _toggle_ipa(self):
        self.show_ipa = not self.show_ipa
        self.ipa_btn.config(text="IPA: ON" if self.show_ipa else "IPA: OFF")
        self._rerender()

    def _toggle_voice_activation(self):
        self._voice_act_enabled = not self._voice_act_enabled
        self.voice_act_btn.config(
            text="Voice Act: ON" if self._voice_act_enabled else "Voice Act: OFF"
        )
        if self.pipeline:
            self.pipeline.voice_act_enabled = self._voice_act_enabled
        if self.wake_detector:
            self.wake_detector.set_listening(self._voice_act_enabled)
            self._update_wake_indicator()

    def _on_sensitivity_changed(self, value):
        self.silence_threshold = float(value)
        self._sensitivity_label_var.set(f"{self.silence_threshold:.4f}")
        if self.pipeline:
            self.pipeline.set_silence_threshold(self.silence_threshold)

    def _rerender(self):
        """Redraw the conversation area from the display log, applying IPA visibility."""
        self.text_area.config(state=tk.NORMAL)
        self.text_area.delete("1.0", tk.END)
        self.text_area.config(state=tk.DISABLED)
        self._current_chunk_start = "1.0"
        for tag, text in self._display_log:
            display = text if self.show_ipa else self._strip_ipa(text)
            self.text_area.config(state=tk.NORMAL)
            self.text_area.insert(tk.END, display, tag)
            self.text_area.config(state=tk.DISABLED)
        self.text_area.see(tk.END)

    def _append(self, tag: str, text: str):
        self._display_log.append((tag, text))
        display = text if self.show_ipa else self._strip_ipa(text)
        self.text_area.config(state=tk.NORMAL)
        self.text_area.insert(tk.END, display, tag)
        self.text_area.see(tk.END)
        self.text_area.config(state=tk.DISABLED)

    # ── click-to-play helpers (all called on main thread) ─────────────────────

    def _init_chunk_tracking(self):
        self._current_chunk_start = self.text_area.index("end-1c")
        self._display_log.append(("__init_chunk__", ""))

    def _finalize_chunk(self, chunk_tag: str):
        end = self.text_area.index("end-1c")
        self._apply_chunk_tag(chunk_tag, self._current_chunk_start, end)
        self._display_log.append(("__chunk__", chunk_tag))
        self._current_chunk_start = end

    def _apply_chunk_tag(self, chunk_tag: str, start: str, end: str):
        self.text_area.tag_add(chunk_tag, start, end)
        self.text_area.tag_bind(chunk_tag, "<Enter>",
            lambda _e, t=chunk_tag: self.text_area.tag_config(t, underline=True))
        self.text_area.tag_bind(chunk_tag, "<Leave>",
            lambda _e, t=chunk_tag: self.text_area.tag_config(t, underline=False))

    def _chunk_tag_at(self, event) -> str | None:
        idx = self.text_area.index(f"@{event.x},{event.y}")
        for tag in self.text_area.tag_names(idx):
            if tag in self._chunk_tag_map:
                return tag
        return None

    def _on_text_click(self, event):
        tag = self._chunk_tag_at(event)
        if tag is None:
            return
        if self._click_job:
            self.root.after_cancel(self._click_job)
        self._click_job = self.root.after(250, self._play_chunk, tag)

    def _on_text_double_click(self, event):
        tag = self._chunk_tag_at(event)
        if tag is None:
            return
        if self._click_job:
            self.root.after_cancel(self._click_job)
            self._click_job = None
        threading.Thread(target=self._play_from_chunk, args=(tag,), daemon=True).start()

    def _play_chunk(self, tag: str):
        self._click_job = None
        turn_idx, chunk_idx = self._chunk_tag_map.get(tag, (None, None))
        if turn_idx is None or self.agent is None:
            return
        try:
            audio = self.agent.audio_turns[turn_idx][chunk_idx]
        except IndexError:
            return
        threading.Thread(
            target=self.agent.play_audio, args=(audio,), kwargs={"stop_first": True},
            daemon=True,
        ).start()

    def _play_from_chunk(self, tag: str):
        turn_idx, chunk_idx = self._chunk_tag_map.get(tag, (None, None))
        if turn_idx is None or self.agent is None:
            return
        try:
            chunks = self.agent.audio_turns[turn_idx][chunk_idx:]
        except IndexError:
            return
        if not chunks:
            return
        audio = np.concatenate(chunks)
        self.agent.play_audio(audio, stop_first=True)

    def _render_history_to_ui(self):
        """Replay _display_log into the text area, re-applying chunk tags at boundaries."""
        if not self._display_log:
            for msg in self.messages:
                role = msg.get("role")
                content = msg.get("content")
                if isinstance(content, str) and role == "user":
                    self._append("user_label", "You: ")
                    self._append("user_text", content + "\n\n")
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict) or block.get("type") != "text":
                            continue
                        text = block["text"]
                        if role == "assistant":
                            self._append("teacher_label", "Teacher: ")
                            self._append("teacher_text", text + "\n\n")
                        elif role == "user":
                            self._append("user_label", "You: ")
                            self._append("user_text", text + "\n\n")
            return

        chunk_start = self.text_area.index("end-1c")
        for tag, text in self._display_log:
            if tag == "__chunk__":
                chunk_end = self.text_area.index("end-1c")
                if chunk_tag_name := text:
                    self._apply_chunk_tag(chunk_tag_name, chunk_start, chunk_end)
                chunk_start = chunk_end
            elif tag == "__init_chunk__":
                chunk_start = self.text_area.index("end-1c")
            else:
                display = text if self.show_ipa else self._strip_ipa(text)
                self.text_area.config(state=tk.NORMAL)
                self.text_area.insert(tk.END, display, tag)
                self.text_area.config(state=tk.DISABLED)
        self.text_area.see(tk.END)

    def _show_error(self, error: str):
        self.status_var.set(f"Error: {error}")
        self.record_btn.config(state=tk.NORMAL)
        self._tts_playing = False
        if self.wake_detector:
            self.wake_detector.set_listening(True)
            self._update_wake_indicator()

    def _on_voice_selected(self, _event=None):
        self.kokoro_voice = self.voice_var.get()
        if self.kokoro_pipeline is not None:
            from kokoro import KPipeline
            lang_code = KOKORO_VOICES[self.kokoro_voice]
            self.kokoro_pipeline = KPipeline(lang_code=lang_code)
        if self.agent:
            self.agent.kokoro_voice = self.kokoro_voice
            self.agent.kokoro_pipeline = self.kokoro_pipeline

    def _on_stt_lang_selected(self, _event=None):
        self.stt_language = WHISPER_LANGUAGES[self.stt_lang_var.get()]
        if self.pipeline:
            self.pipeline.set_stt_language(self.stt_language)

    def _on_accent_selected(self, _event=None):
        self.accent = self.accent_var.get()
        if self.agent:
            self.agent.accent = self.accent

    def _on_stt_model_selected(self, _event=None):
        new_size = self.stt_model_var.get()
        if new_size == self.model_size:
            return
        self.model_size = new_size
        self.status_var.set(f"Loading STT model {new_size}…")

        def _reload():
            try:
                if self.backend_name == "whisperx":
                    new_backend = WhisperXBackend(new_size)
                else:
                    new_backend = FasterWhisperBackend(new_size)
                self.stt_backend = new_backend
                if self.pipeline:
                    self.pipeline.set_stt_backend(new_backend)
                self.root.after(0, self.status_var.set, f"STT model: {new_size}")
            except Exception as e:
                self.root.after(0, self._show_error, f"STT load failed: {e}")

        threading.Thread(target=_reload, daemon=True).start()

    def copy_text(self):
        text = self.text_area.get("1.0", tk.END).strip()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set("Copied.")
        self.root.after(1500, lambda: self.status_var.set("Ready"))

    def clear_conversation(self):
        self.messages.clear()
        self._display_log.clear()
        self._chunk_tag_map.clear()
        if self.agent:
            self.agent.clear_audio_turns()
        if self.pdf_path:
            LessonStore.audio_path(self.pdf_path).unlink(missing_ok=True)
        self.text_area.config(state=tk.NORMAL)
        self.text_area.delete("1.0", tk.END)
        self.text_area.config(state=tk.DISABLED)


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    available = detect_available_backends()
    if not available:
        print("No STT backend found. Install faster-whisper:")
        print("  uv add faster-whisper")
        return

    parser = argparse.ArgumentParser(description="Agentic document teacher (voice)")
    if len(available) > 1:
        parser.add_argument(
            "--backend", choices=available, default=available[0],
            help=f"STT backend. Default: {available[0]}",
        )
    parser.add_argument("--model", default="base", metavar="SIZE",
                        help="Whisper model size (default: base)")
    parser.add_argument("--voice", default=DEFAULT_KOKORO_VOICE, choices=list(KOKORO_VOICES),
                        help=f"Kokoro TTS voice (default: {DEFAULT_KOKORO_VOICE})")
    parser.add_argument("--llm-model", default=DEFAULT_LLM_MODEL, metavar="MODEL",
                        help=f"Claude model (default: {DEFAULT_LLM_MODEL})")
    parser.add_argument(
        "--wake-word", default=None, metavar="WORD",
        help=(
            "Enable wake-word activation. Built-in words: "
            "hey_jarvis, hey_mycroft, alexa, timer, weather. "
            "Default: disabled."
        ),
    )
    parser.add_argument(
        "--silence-timeout", type=float, default=5.0, metavar="SECS",
        help="Seconds of silence before recording stops automatically (default: 5.0)",
    )
    parser.add_argument(
        "--silence-threshold", type=float, default=SILENCE_THRESHOLD, metavar="RMS",
        help=(
            f"RMS level below which audio is treated as silence "
            f"(default: {SILENCE_THRESHOLD}, ≈ −50 dB) (Formula: 10^(dB/20))"
        ),
    )
    args = parser.parse_args()

    backend_name = getattr(args, "backend", available[0])
    if args.wake_word:
        print(f"Wake word: {args.wake_word}, silence timeout: {args.silence_timeout}s")

    root = tk.Tk()
    root.geometry("700x620")
    TeachApp(root, backend_name, args.model, args.voice, args.llm_model,
             args.wake_word, args.silence_timeout, args.silence_threshold)
    root.mainloop()


if __name__ == "__main__":
    main()
