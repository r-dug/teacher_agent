"""
TeacherAgent — orchestrator for the agentic teaching loop.

Responsibilities:
- Drive the LLM turn loop via an LLMProvider.
- Pipeline text to TTSPipeline for synthesis and playback.
- Dispatch tool results to callbacks and manage message history.
- Expose prepare_for_tts / generate_instructions (LLM-backed helpers).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable

import numpy as np

from ...db.util import _strip_dangling_tool_use
from ..voice.config import KOKORO_SAMPLE_RATE
from ..voice.phonetics import ACCENT_PROFILES, DEFAULT_ACCENT, IPA_REFERENCE, replace_roman_numerals
from .agent import Agent
from .callbacks import TeachingCallbacks
from .config import DEFAULT_LLM_MODEL
from .curriculum import Curriculum
from .prompts.persona import CONDENSE_EPISODE_SYSTEM, GENERATE_INSTRUCTIONS_SYSTEM
from .prompts.teaching import make_intro_prompt, make_teaching_prompt
from .prompts.tts_prep import TTS_PREP_USER_PREFIX, make_tts_prep_system
from .providers.base import LLMProvider
from .tools import CAPTURE_GOAL_TOOL, GENERATE_VISUAL_AID_TOOL, TEACHING_TOOLS
from .tts_pipeline import TTSPipeline

log = logging.getLogger(__name__)


class TeacherAgent(Agent):
    """
    Orchestrates an agentic teaching session.

    All blocking operations (LLM calls, TTS synthesis, audio playback) are
    synchronous.  Callers must invoke run_turn() from background threads.

    Callbacks are fired from the calling thread.
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        callbacks: TeachingCallbacks,
        tts_providers: list | None = None,
        tts_voice: str = "",
        model: str = DEFAULT_LLM_MODEL,
        accent: str = DEFAULT_ACCENT,
    ) -> None:
        super().__init__(model)
        self._provider = llm_provider
        self._callbacks = callbacks
        self.tts_providers: list = list(tts_providers) if tts_providers else []
        self.tts_voice = tts_voice
        self.accent = accent

        self._audio_turns: list[list[np.ndarray]] = []
        self.last_audio: np.ndarray | None = None

    # ── public API ─────────────────────────────────────────────────────────────

    @property
    def _effective_tools(self) -> list:
        """TEACHING_TOOLS extended with generate_visual_aid when the callback is set."""
        if self._callbacks.on_generate_visual_aid is not None:
            return TEACHING_TOOLS + [GENERATE_VISUAL_AID_TOOL]
        return TEACHING_TOOLS

    @property
    def audio_turns(self) -> list[list[np.ndarray]]:
        return self._audio_turns

    def set_tts_voice(self, voice: str) -> None:
        self.tts_voice = voice

    def run_intro_turn(
        self,
        curriculum: Curriculum,
        messages: list[dict],
        raw_text: str | None = None,
    ) -> str | None:
        """
        Run one turn of the agentic goal-gathering intro loop.

        Returns the captured lesson goal string if the agent called
        capture_lesson_goal, otherwise None (agent asked a follow-up).
        Modifies messages in-place.
        """
        if not messages:
            messages.append({"role": "user", "content": "Please begin."})

        tool = self._do_single_llm_turn(
            curriculum,
            messages,
            agent_instructions=None,
            _system=make_intro_prompt(curriculum.title, curriculum.sections, raw_text),
            _tools=[CAPTURE_GOAL_TOOL],
            _call_type="intro_turn",
        )

        if tool is not None and getattr(tool, "name", None) == "capture_lesson_goal":
            goal = (tool.input or {}).get("goal", "").strip()
            depth = (tool.input or {}).get("depth", "")
            messages.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool.id, "content": "Goal captured."}],
            })
            if self._callbacks.on_turn_complete:
                self._callbacks.on_turn_complete(self.last_audio)
            return f"{goal} (level: {depth})" if depth else (goal or "Learn the material.")

        if tool is not None:
            messages.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool.id, "content": "OK"}],
            })
        if self._callbacks.on_turn_complete:
            self._callbacks.on_turn_complete(self.last_audio)
        return None

    def run_intro(self, curriculum: Curriculum, messages: list[dict], raw_text: str | None = None) -> str | None:
        """Backward-compat alias for run_intro_turn."""
        return self.run_intro_turn(curriculum, messages, raw_text)

    def run_turn(
        self,
        curriculum: Curriculum,
        messages: list[dict],
        agent_instructions: str | None,
        lesson_goal: str | None = None,
    ) -> None:
        """
        Run the agentic teaching loop.  May chain multiple LLM calls via tool use.
        Synchronous; call from a background thread.  Modifies messages in-place.
        """
        if not curriculum.sections:
            raise ValueError(
                "No lesson sections are available for teaching. Decompose the document first."
            )

        if not messages:
            messages.append({"role": "user", "content": "Please begin teaching."})

        _strip_dangling_tool_use(messages)

        while True:
            tool = self._do_single_llm_turn(
                curriculum, messages, agent_instructions, lesson_goal=lesson_goal,
                _tools=self._effective_tools,
            )

            if tool is None:
                if self._callbacks.on_turn_complete:
                    self._callbacks.on_turn_complete(self.last_audio)
                return

            _INTERACTIVE_TOOLS = frozenset({
                "open_sketchpad", "take_photo", "record_video",
                "open_code_editor", "open_html_editor", "start_timer",
                "generate_visual_aid",
            })
            if tool.name in _INTERACTIVE_TOOLS:
                pending_assistant_msg = messages.pop()
            else:
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool.id, "content": "OK"}],
                })
                pending_assistant_msg = None

            if tool.name == "advance_to_next_section":
                if curriculum.is_last:
                    if self._callbacks.on_curriculum_complete:
                        self._callbacks.on_curriculum_complete()
                    return
                curriculum.idx += 1
                if self._callbacks.on_section_advanced:
                    self._callbacks.on_section_advanced(curriculum)
                episode_summary = self._condense_episode(messages, curriculum)
                messages.clear()
                if episode_summary:
                    messages.append({
                        "role": "user",
                        "content": (
                            "[Student profile from the previous section — use this to personalise "
                            "your teaching style and pacing for this section]:\n"
                            f"{episode_summary}"
                        ),
                    })

            elif tool.name == "show_slide":
                page_start = tool.input.get("page_start", 1)
                page_end = tool.input.get("page_end", page_start)
                caption = tool.input.get("caption", "")
                if self._callbacks.on_show_slide:
                    self._callbacks.on_show_slide(page_start, page_end, caption)

            elif tool.name == "open_sketchpad":
                prompt = tool.input.get("prompt", "Please draw:")
                text_bg: str | None = tool.input.get("text_bg")
                bg_page: int | None = tool.input.get("bg_page")
                result_holder: list[str | None] = [None]
                done_event = threading.Event()
                if self._callbacks.on_open_sketchpad:
                    self._callbacks.on_open_sketchpad(prompt, result_holder, done_event, text_bg, bg_page)
                done_event.wait()
                messages.append(pending_assistant_msg)
                if result_holder[0] is None:
                    tool_content: list | str = "The student dismissed the drawing tool without submitting."
                else:
                    tool_content = [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": result_holder[0]}},
                        {"type": "text", "text": "The student has submitted their drawing. Please evaluate it and give feedback."},
                    ]
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool.id, "content": tool_content}],
                })

            elif tool.name == "take_photo":
                prompt = tool.input.get("prompt", "Please take a photo.")
                result_holder_p: list[str | None] = [None]
                done_event_p = threading.Event()
                if self._callbacks.on_take_photo:
                    self._callbacks.on_take_photo(prompt, result_holder_p, done_event_p)
                done_event_p.wait()
                messages.append(pending_assistant_msg)
                if result_holder_p[0] is None:
                    photo_content: list | str = "The student dismissed the photo tool without taking a photo."
                else:
                    photo_content = [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": result_holder_p[0]}},
                        {"type": "text", "text": "The student has taken a photo. Please observe and respond."},
                    ]
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool.id, "content": photo_content}],
                })

            elif tool.name == "record_video":
                prompt = tool.input.get("prompt", "Please record a short video.")
                result_holder_v: list[list[str] | None] = [None]
                done_event_v = threading.Event()
                if self._callbacks.on_record_video:
                    self._callbacks.on_record_video(prompt, result_holder_v, done_event_v)
                done_event_v.wait()
                frames: list[str] = result_holder_v[0] or []
                frame_blocks = [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": frame}}
                    for frame in frames
                ]
                frame_blocks.append({
                    "type": "text",
                    "text": (
                        f"The student recorded a video. These are {len(frames)} evenly-spaced "
                        "frames sampled from the recording. Please observe the sequence and respond."
                    ),
                })
                messages.append(pending_assistant_msg)
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool.id, "content": frame_blocks}],
                })

            elif tool.name == "open_code_editor":
                prompt = tool.input.get("prompt", "Complete the coding challenge.")
                language = tool.input.get("language", "python")
                starter_code: str | None = tool.input.get("starter_code")
                result_holder_ce: list[dict | None] = [None]
                done_event_ce = threading.Event()
                if self._callbacks.on_open_code_editor:
                    self._callbacks.on_open_code_editor(prompt, language, starter_code, result_holder_ce, done_event_ce)
                done_event_ce.wait()
                r = result_holder_ce[0] or {}
                content = (
                    f"The student submitted the following {language} code:\n\n"
                    f"```{language}\n{r.get('code', '')}\n```\n\n"
                    f"Execution output:\n"
                    f"stdout: {r.get('stdout', '') or '(none)'}\n"
                    f"stderr: {r.get('stderr', '') or '(none)'}\n"
                    f"exit code: {r.get('exit_code', -1)}"
                )
                messages.append(pending_assistant_msg)
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool.id, "content": content}],
                })

            elif tool.name == "open_html_editor":
                prompt = tool.input.get("prompt", "Complete the HTML/CSS challenge.")
                starter_html: str | None = tool.input.get("starter_html")
                starter_css: str | None = tool.input.get("starter_css")
                result_holder_he: list[dict | None] = [None]
                done_event_he = threading.Event()
                if self._callbacks.on_open_html_editor:
                    self._callbacks.on_open_html_editor(prompt, starter_html, starter_css, result_holder_he, done_event_he)
                done_event_he.wait()
                r = result_holder_he[0] or {}
                content = (
                    f"The student submitted the following HTML/CSS:\n\n"
                    f"HTML:\n```html\n{r.get('html', '')}\n```\n\n"
                    f"CSS:\n```css\n{r.get('css', '')}\n```"
                )
                messages.append(pending_assistant_msg)
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool.id, "content": content}],
                })

            elif tool.name == "start_timer":
                prompt = tool.input.get("prompt", "Complete the exercise.")
                duration_seconds = int(tool.input.get("duration_seconds", 60))
                result_holder_ti: list[dict | None] = [None]
                done_event_ti = threading.Event()
                if self._callbacks.on_start_timer:
                    self._callbacks.on_start_timer(prompt, duration_seconds, result_holder_ti, done_event_ti)
                done_event_ti.wait()
                r = result_holder_ti[0] or {}
                timed_out: bool = r.get("timed_out", True)
                answer: str = (r.get("answer") or "").strip()
                elapsed: int | None = r.get("elapsed_seconds")
                if timed_out:
                    content = (
                        f"Time expired. The student's answer: {answer}"
                        if answer else "Time expired. The student did not submit an answer."
                    )
                else:
                    time_str = f"{elapsed}s" if elapsed is not None else "early"
                    content = (
                        f"The student submitted early ({time_str}). Answer: {answer}"
                        if answer else f"The student submitted early ({time_str}) without writing an answer."
                    )
                messages.append(pending_assistant_msg)
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool.id, "content": content}],
                })

            elif tool.name == "generate_visual_aid":
                prompt = tool.input.get("prompt", "")
                caption = tool.input.get("caption", "")
                result_holder_img: list[str | None] = [None]
                done_event_img = threading.Event()
                if self._callbacks.on_generate_visual_aid:
                    self._callbacks.on_generate_visual_aid(
                        prompt, caption, tool.id, result_holder_img, done_event_img
                    )
                done_event_img.wait()
                # result_holder_img[0] is a public image URL on success, None on failure
                image_url = result_holder_img[0]
                messages.append(pending_assistant_msg)
                if image_url:
                    content = "Image generated successfully and is now displayed to the student."
                else:
                    content = (
                        "Image generation failed. Continue teaching without a visual aid. "
                        "Let the student know briefly that the image could not be generated."
                    )
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool.id, "content": content}],
                })

            elif tool.name == "mark_curriculum_complete":
                if self._callbacks.on_curriculum_complete:
                    self._callbacks.on_curriculum_complete()
                return

    def clear_audio_turns(self) -> None:
        self._audio_turns.clear()

    def generate_instructions(self, description: str) -> str:
        """Generate teacher persona instructions from a description.  Synchronous."""
        import anthropic
        client = anthropic.Anthropic(max_retries=6)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=GENERATE_INSTRUCTIONS_SYSTEM,
            messages=[{"role": "user", "content": f"Teaching style: {description}"}],
        )
        if self._callbacks.on_token_usage:
            self._callbacks.on_token_usage("generate_instructions", "claude-sonnet-4-6", response.usage)
        return response.content[0].text.strip()

    def prepare_for_tts(self, text: str) -> str:
        """Annotate text with IPA for Kokoro TTS.  Synchronous."""
        text = re.sub(r"\*+", "", text)
        text = replace_roman_numerals(text)
        try:
            import anthropic as _anthropic
            client = _anthropic.Anthropic(max_retries=6)
            tts_system = make_tts_prep_system(self.accent, ACCENT_PROFILES, DEFAULT_ACCENT, IPA_REFERENCE)
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                system=[{"type": "text", "text": tts_system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": TTS_PREP_USER_PREFIX + text}],
            )
            if self._callbacks.on_token_usage:
                self._callbacks.on_token_usage("tts_prep", "claude-sonnet-4-6", response.usage)
            result = response.content[0].text.strip()
            result = re.sub(r'\[([^"\]]+)\]\((/[^/]*/)\)', r'["\1"](\2)', result)
            return result
        except Exception:
            return text

    # ── internals ──────────────────────────────────────────────────────────────

    def _condense_episode(self, messages: list[dict], curriculum: Curriculum) -> str:
        """Condense the completed section's conversation into a brief student profile."""
        completed_section = curriculum.sections[curriculum.idx - 1]

        transcript_parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, str):
                if content.strip():
                    transcript_parts.append(f"{role.upper()}: {content.strip()}")
            elif isinstance(content, list):
                texts = [
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                combined = " ".join(t for t in texts if t).strip()
                if combined:
                    transcript_parts.append(f"{role.upper()}: {combined}")

        if not transcript_parts:
            return ""

        result = self._provider.do_turn(
            model=self._model,
            system=CONDENSE_EPISODE_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Completed section: \"{completed_section.get('title', 'Unknown')}\"\n\n"
                    f"Transcript:\n{chr(10).join(transcript_parts)}\n\n"
                    "Write the student profile for the incoming teacher."
                ),
            }],
            tools=[],
        )
        if self._callbacks.on_token_usage:
            self._callbacks.on_token_usage("episode_condensation", self._model, result.usage)
        return result.content_text.strip()

    def _do_single_llm_turn(
        self,
        curriculum: Curriculum,
        messages: list[dict],
        agent_instructions: str | None,
        lesson_goal: str | None = None,
        _system: str | None = None,
        _tools: list | None = None,
        _call_type: str | None = None,
    ):
        """
        Execute one LLM turn, pipeline TTS in parallel threads.

        For streaming providers (Anthropic), TTS synthesis starts while the LLM
        is still generating — the on_text_chunk closure accumulates a buffer and
        flushes completed lines to the TTSPipeline in real time.

        Appends the assistant message to messages.
        Returns the first tool_use block (as a SimpleNamespace or SDK object), or None.
        """
        if _system is not None:
            system = _system
        else:
            system = make_teaching_prompt(
                curriculum.title, curriculum.sections, curriculum.idx, lesson_goal
            )
            if agent_instructions:
                system += f"\n\nADDITIONAL STYLE INSTRUCTIONS:\n{agent_instructions}"
        tools = self._effective_tools if _tools is None else _tools
        call_type = _call_type or ("intro_turn" if _tools == [] else "teach_turn")

        # Build TTSPipeline for this turn.
        tts = TTSPipeline(
            providers=self.tts_providers,
            callbacks=self._callbacks,
            preprocess_fn=self.prepare_for_tts,
            tts_voice=self.tts_voice,
        )
        turn_idx = len(self._audio_turns)
        turn_chunks: list[np.ndarray] = []
        self._audio_turns.append(turn_chunks)
        tts._audio_chunks = turn_chunks  # share reference so turn_chunks gets populated
        tts.start_turn(turn_idx)

        # Per-turn mutable state captured by closures below.
        chunk_counter = [0]
        text_buf = [""]

        def _flush_line(line: str) -> None:
            line = line.strip()
            if not line:
                return
            idx = chunk_counter[0]
            chunk_counter[0] += 1
            tts.queue_text(f"t{turn_idx}c{idx}", line, idx)

        def _on_text_chunk(chunk: str) -> None:
            """Forward chunk to WS and buffer for real-time TTS flushing."""
            if self._callbacks.on_text_chunk:
                self._callbacks.on_text_chunk(chunk)
            text_buf[0] += chunk
            if "\n" in text_buf[0]:
                parts = text_buf[0].split("\n")
                for part in parts[:-1]:
                    _flush_line(part)
                text_buf[0] = parts[-1]
            elif len(text_buf[0].split()) >= 400:
                _flush_line(text_buf[0])
                text_buf[0] = ""

        try:
            if self._callbacks.on_turn_start:
                self._callbacks.on_turn_start()

            _llm_t0 = time.monotonic()
            result = self._provider.do_turn(
                model=self._model,
                system=system,
                messages=messages,
                tools=tools,
                on_text_chunk=_on_text_chunk,
            )
            _llm_elapsed = time.monotonic() - _llm_t0

            # Flush any remainder after the stream ends.
            if text_buf[0].strip():
                _flush_line(text_buf[0])

            if self._callbacks.on_token_usage:
                self._callbacks.on_token_usage(call_type, self._model, result.usage)

            _usage = result.usage
            log.info(
                "[llm] provider=%s model=%s call=%s tokens_in=%s tokens_out=%s elapsed=%.2fs",
                self._provider.name,
                self._model,
                call_type,
                getattr(_usage, "input_tokens", "?"),
                getattr(_usage, "output_tokens", "?"),
                _llm_elapsed,
            )

            messages.append({"role": "assistant", "content": result.content_blocks})

        except Exception as e:
            log.exception("_do_single_llm_turn: LLM exception: %s", e)
            if self._callbacks.on_error:
                self._callbacks.on_error(str(e))
            tts.shutdown()
            return None

        _tts_t0 = time.monotonic()
        audio_chunks = tts.finish()
        self.tts_voice = tts.tts_voice  # pick up any voice updates from synthesis

        log.info("[tts] chunks=%d elapsed=%.2fs", len(audio_chunks), time.monotonic() - _tts_t0)

        all_audio = [c for c in audio_chunks if c.size > 0]
        if all_audio:
            self.last_audio = np.concatenate(all_audio)

        if self._callbacks.on_response_end:
            self._callbacks.on_response_end()

        return result.tool_use
