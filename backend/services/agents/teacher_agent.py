"""
TeacherAgent — orchestrator for the agentic teaching loop.

Responsibilities:
- Drive the LLM turn loop via an LLMProvider.
- Pipeline text to TTSPipeline for synthesis and playback.
- Dispatch tool results to callbacks and manage message history.
- Expose prepare_for_tts / generate_instructions (LLM-backed helpers).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable

import numpy as np

# Plan B: per-LLM-call timeout (seconds).  Wraps the streaming SDK call so
# a hung provider doesn't strand run_turn.  Tool waits remain unbounded —
# the student can take as long as they need.
_LLM_CALL_TIMEOUT = 60.0

from ...db.util import _strip_dangling_tool_use
from ..voice.config import KOKORO_SAMPLE_RATE
from ..voice.phonetics import ACCENT_PROFILES, DEFAULT_ACCENT, IPA_REFERENCE, replace_roman_numerals
from .agent import Agent
from .callbacks import AgentCallbacks
from .config import DEFAULT_LLM_MODEL
from .curriculum import Curriculum
from .prompts.persona import CONDENSE_EPISODE_SYSTEM, GENERATE_INSTRUCTIONS_SYSTEM
from .prompts.teaching import make_intro_prompt, make_teaching_prompt
from .prompts.tts_prep import TTS_PREP_USER_PREFIX, make_tts_prep_system
from .providers.base import LLMProvider
from .memory_strategies import STRATEGIES as MEMORY_STRATEGIES, TurnSummaryTracker, EmbeddingCache, strategy_turn_summaries
from .tool_registry import INTERACTIVE_TOOL_NAMES, TOOL_REGISTRY
from .tools import CAPTURE_GOAL_TOOL, GENERATE_VISUAL_AID_TOOL, SEARCH_IMAGE_TOOL, TEACHING_TOOLS
from .tts_pipeline import TTSPipeline

# Plan B Commit 4: re-export the registry's interactive tool names under the
# old private name for backward compat with any internal code referencing it.
_INTERACTIVE_TOOL_NAMES = INTERACTIVE_TOOL_NAMES

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
        callbacks: AgentCallbacks,
        tts_providers: list | None = None,
        tts_voice: str = "",
        model: str = DEFAULT_LLM_MODEL,
        accent: str = DEFAULT_ACCENT,
        memory_strategy: str = "turn_summaries",
    ) -> None:
        super().__init__(model)
        self._provider = llm_provider
        self._callbacks = callbacks
        self.tts_providers: list = list(tts_providers) if tts_providers else []
        self.tts_voice = tts_voice
        self.accent = accent
        self._memory_strategy_name = memory_strategy
        self._memory_strategy = MEMORY_STRATEGIES.get(memory_strategy, strategy_turn_summaries)
        self._turn_tracker = TurnSummaryTracker()
        self._embedding_cache = EmbeddingCache()
        if memory_strategy == "rag_semantic":
            self._init_embeddings()

        self._audio_turns: list[list[np.ndarray]] = []
        self.last_audio: np.ndarray | None = None
        # Plan B: WS-layer turn id, set by run_turn() at entry; passed to
        # the session's on_dispatch_context callback before each interactive
        # tool dispatch so the persisted pending row carries it.
        self._current_turn_id: str = ""

    def _init_embeddings(self) -> None:
        """Set up OpenAI embedding function for RAG strategy.

        Plan B follow-up A2: replaces inline ``openai.OpenAI(...)``
        construction with ``OpenAIEmbeddingProvider`` configured from
        ``EMBEDDING_CHAIN``.  Centralizes the embedding model name and
        retry/timeout config.
        """
        try:
            import os

            from .model_chains import EMBEDDING_CHAIN
            from .providers.openai_embeddings import OpenAIEmbeddingProvider

            spec = EMBEDDING_CHAIN.primary
            api_key_env = spec.source_config.get("api_key_env", "OPENAI_API_KEY")
            api_key = os.environ.get(api_key_env, "").strip()
            if not api_key:
                raise ValueError(f"Embedding requires {api_key_env} to be set.")

            provider = OpenAIEmbeddingProvider(
                api_key=api_key,
                model=spec.name,
                timeout_seconds=float(spec.source_config.get("timeout_s", 10.0)),
                max_retries=int(spec.source_config.get("max_retries", 1)),
            )
            self._embedding_cache.set_embed_fn(provider.embed)
            log.info("RAG semantic: OpenAI embeddings initialized (model=%s)", spec.name)
        except Exception:
            log.warning("RAG semantic: failed to init embeddings, falling back to recency")

    # ── public API ─────────────────────────────────────────────────────────────

    @property
    def _effective_tools(self) -> list:
        """TEACHING_TOOLS extended with optional image tools when capability
        flags are set on the callbacks (Plan B Commit 4: was per-callback
        gating, now flag gating since the interactive callback is unified)."""
        tools = list(TEACHING_TOOLS)
        if self._callbacks.image_gen_enabled:
            tools.append(GENERATE_VISUAL_AID_TOOL)
        if self._callbacks.image_search_enabled:
            tools.append(SEARCH_IMAGE_TOOL)
        return tools

    @property
    def audio_turns(self) -> list[list[np.ndarray]]:
        return self._audio_turns

    def set_tts_voice(self, voice: str) -> None:
        self.tts_voice = voice

    async def run_intro_turn(
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

        Plan B: async-native.  The single LLM call hops to a worker thread
        via asyncio.to_thread (the SDK is sync) and is bounded by
        ``_LLM_CALL_TIMEOUT``.
        """
        # check if messages exist
        if not messages:
            messages.append({"role": "user", "content": "Please begin."})

        tools = await asyncio.wait_for(
            asyncio.to_thread(
                self._do_single_llm_turn,
                curriculum,
                messages,
                None,  # agent_instructions
                lesson_goal=None,
                _system=make_intro_prompt(
                    curriculum.title, curriculum.sections, raw_text,
                ),
                _tools=[CAPTURE_GOAL_TOOL],
                _call_type="intro_turn",
            ),
            timeout=_LLM_CALL_TIMEOUT,
        )
        # Intro only ever has one expected tool (capture_lesson_goal); take the first.
        tool = tools[0] if tools else None

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

    async def run_intro(self, curriculum: Curriculum, messages: list[dict], raw_text: str | None = None) -> str | None:
        """Backward-compat alias for run_intro_turn."""
        return await self.run_intro_turn(curriculum, messages, raw_text)

    async def run_turn(
        self,
        curriculum: Curriculum,
        messages: list[dict],
        agent_instructions: str | None,
        lesson_goal: str | None = None,
        turn_id: str = "",
    ) -> None:
        """
        Run the agentic teaching loop.  May chain multiple LLM calls via tool use.

        Plan B: async-native.  Runs entirely on the event loop except for the
        LLM streaming call (which still hops to a thread because the SDK is
        sync).  Each interactive tool dispatch awaits an async callback that
        registers a future + sends the open_* event + awaits the student's
        submission.  No threading.Event, no result_holder list, no
        cross-thread blocking.

        On WS disconnect the surrounding ``agent_task`` is cancelled, which
        propagates ``CancelledError`` through every awaited callback and
        unwinds the loop cleanly.

        ``turn_id`` is the WS-layer turn identifier; the dispatcher uses it
        to persist pending interactive-tool state to PG (decision 5 of
        Plan B), so the same turn can be resumed across reconnect / device
        switch.  Pass an empty string when there's no client to resume to
        (e.g., from tests).

        Modifies ``messages`` in place.
        """
        self._current_turn_id = turn_id
        if not curriculum.sections:
            raise ValueError(
                "No lesson sections are available for teaching. Decompose the document first."
            )

        if not messages:
            messages.append({"role": "user", "content": "Please begin teaching."})

        # Record student's input for turn summary tracking
        if messages and messages[-1].get("role") == "user":
            from .memory_strategies import _extract_text
            self._turn_tracker.record_student(_extract_text(messages[-1].get("content", "")))

        _strip_dangling_tool_use(messages)

        is_first_call = True
        shared_turn_idx: list[int | None] = [None]  # filled by first call
        _MAX_LOOP = 10  # safety: prevent infinite LLM loops
        _loop_iter = 0
        while True:
            _loop_iter += 1

            # ── advance check ─────────────────────────────────────────────
            # If all tasks are done (either from a previous turn or from a
            # mark_task_complete earlier in this loop), advance now.
            if curriculum.all_tasks_done():
                if curriculum.is_last:
                    log.info("mid-loop: final section complete — curriculum done")
                    if self._callbacks.on_curriculum_complete:
                        self._callbacks.on_curriculum_complete()
                    return
                prev_idx = curriculum.idx
                curriculum.advance()
                log.info("mid-loop advance: %d → %d", prev_idx, curriculum.idx)
                if self._callbacks.on_section_advanced:
                    self._callbacks.on_section_advanced(curriculum)
                # _condense_episode runs the sync LLM SDK; hop to a thread so
                # we don't block the event loop.  Same per-call timeout as the
                # main LLM call (decision 6 of Plan B).
                try:
                    episode_summary = await asyncio.wait_for(
                        asyncio.to_thread(self._condense_episode, list(messages), curriculum),
                        timeout=_LLM_CALL_TIMEOUT,
                    )
                except (asyncio.TimeoutError, Exception) as exc:
                    log.warning("episode condensation failed: %s", exc)
                    episode_summary = ""
                messages.clear()
                self._turn_tracker.clear()
                self._embedding_cache.clear()
                if episode_summary:
                    messages.append({
                        "role": "user",
                        "content": (
                            "[Student profile from the previous section — use this to personalise "
                            "your teaching style and pacing for this section]:\n"
                            f"{episode_summary}"
                        ),
                    })
                if not messages:
                    messages.append({"role": "user", "content": "Please begin teaching."})
                is_first_call = True
                shared_turn_idx[0] = None

            if _loop_iter > _MAX_LOOP:
                log.warning("run_turn: hit loop limit (%d) — ending turn", _MAX_LOOP)
                if self._callbacks.on_turn_complete:
                    self._callbacks.on_turn_complete(self.last_audio)
                return
            log.info("run_turn loop iter=%d section=%d messages=%d", _loop_iter, curriculum.idx, len(messages))

            # LLM call: hop to a thread (sync SDK) and bound it with a hard
            # per-call timeout so a hung provider can't strand the loop.
            tools = await asyncio.wait_for(
                asyncio.to_thread(
                    self._do_single_llm_turn,
                    curriculum,
                    messages,
                    agent_instructions,
                    lesson_goal=lesson_goal,
                    _tools=self._effective_tools,
                    _suppress_turn_start=not is_first_call,
                    _turn_idx_override=shared_turn_idx,
                ),
                timeout=_LLM_CALL_TIMEOUT,
            )
            is_first_call = False

            if not tools:
                log.info("run_turn: no tool — ending turn (iter=%d)", _loop_iter)
                # Record the assistant's text from the message just appended
                last_content = messages[-1].get("content", "") if messages else ""
                if isinstance(last_content, list):
                    last_content = " ".join(b.get("text", "") for b in last_content if isinstance(b, dict) and b.get("type") == "text")
                self._turn_tracker.record_text(last_content)
                if self._callbacks.on_turn_complete:
                    self._callbacks.on_turn_complete(self.last_audio)
                return

            log.info(
                "run_turn: %d tool(s) (iter=%d): %s",
                len(tools), _loop_iter, [t.name for t in tools],
            )

            # Plan B multi-tool dispatch: iterate every tool_use in the
            # response in declaration order, build a tool_result block for
            # each, and append them all as ONE user message at the end.
            # The Anthropic & OpenAI APIs require a tool_result for every
            # tool_use in the assistant message — collecting them this way
            # guarantees that invariant.
            collected_results = await self._dispatch_tools(
                tools, curriculum, shared_turn_idx,
            )
            if collected_results:
                messages.append({"role": "user", "content": collected_results})
            # If any tool was interactive, the next LLM call should start a
            # fresh visible bubble (decision 1a).  _dispatch_tools resets
            # shared_turn_idx for us when it dispatches an interactive tool;
            # we just need to flag is_first_call so the turn_start event is
            # emitted on the next LLM call too.
            if any(t.name in _INTERACTIVE_TOOL_NAMES for t in tools):
                is_first_call = True

    async def _dispatch_tools(
        self,
        tools: list,
        curriculum: Curriculum,
        shared_turn_idx: list[int | None],
    ) -> list[dict]:
        """Dispatch every tool_use from one LLM response, collect tool_result
        blocks, return them.  Caller appends as one user message.

        Plan B: each interactive tool dispatch resets ``shared_turn_idx[0]``
        to ``None`` so the next LLM text starts a fresh bubble.  Pure tools
        (mark_task / search / progress / audio) leave the bubble alone.
        """
        collected: list[dict] = []
        for tool in tools:
            self._turn_tracker.record_tool(tool.name, tool.input or {})
            block = await self._dispatch_one_tool(tool, curriculum, shared_turn_idx)
            collected.append(block)
        return collected

    async def _dispatch_one_tool(
        self,
        tool,
        curriculum: Curriculum,
        shared_turn_idx: list[int | None],
    ) -> dict:
        """Dispatch a single tool via the registry and return its tool_result.

        Plan B Commit 4: replaces the 330-line if/elif chain with a single
        registry lookup.  Each spec's handler knows how to construct its
        tool_result block — see ``backend/services/agents/tool_registry.py``.
        """
        spec = TOOL_REGISTRY.get(tool.name)
        if spec is None:
            log.warning("run_turn: unknown tool %s — surfacing as error", tool.name)
            return {
                "type": "tool_result",
                "tool_use_id": tool.id,
                "content": f"Unknown tool {tool.name}",
                "is_error": True,
            }

        # Interactive tools end the visible bubble (Plan B decision 1a) so
        # the next LLM text starts a fresh bubble.  Curriculum/pure tools
        # leave the bubble alone.
        if spec.kind == "interactive":
            shared_turn_idx[0] = None

        return await spec.handler(self, tool, curriculum)

    @staticmethod
    def build_resumed_tool_result(
        tool_use_id: str,
        tool_name: str,
        tool_input: dict,
        raw_result: dict | None,
    ) -> dict:
        """Plan B Commit 4: build a tool_result block for a previously
        persisted interactive tool whose student submission has just arrived.

        Used by the resume path: ws_session loaded the pending row, awaited
        the future, and now needs to convert the raw client dict into the
        same content block ``_dispatch_one_tool`` would have produced if
        the original run_turn hadn't been interrupted.

        Reuses the registry's ``build_result`` lambda so the resume path
        and the live dispatch path produce identical content.  Falls back
        to a generic error block if the tool name isn't in the registry
        or its spec lacks a build_result (curriculum/pure tools — they
        don't normally end up in pending_tool_invocations).
        """
        spec = TOOL_REGISTRY.get(tool_name)
        if spec is None or spec.build_result is None:
            log.warning(
                "build_resumed_tool_result: %s has no build_result — using generic OK",
                tool_name,
            )
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": "OK" if raw_result else "Tool was dismissed.",
            }
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": spec.build_result(raw_result, tool_input or {}),
        }

    def clear_audio_turns(self) -> None:
        self._audio_turns.clear()

    def generate_instructions(self, description: str) -> str:
        """Generate teacher persona instructions from a description.

        Plan B follow-up A2: uses ``provider.complete()`` via
        ``INSTRUCTIONS_CHAIN`` instead of inline ``anthropic.Anthropic(...)``.
        """
        from .model_chains import INSTRUCTIONS_CHAIN
        from .model_config import build_chain

        provider = build_chain(INSTRUCTIONS_CHAIN)
        text, usage = provider.complete(
            system=GENERATE_INSTRUCTIONS_SYSTEM,
            messages=[{"role": "user", "content": f"Teaching style: {description}"}],
            max_tokens=512,
        )
        if self._callbacks.on_token_usage:
            self._callbacks.on_token_usage("generate_instructions", provider.model, usage)
        return text

    def prepare_for_tts(self, text: str) -> str:
        """Annotate text with IPA for Kokoro TTS.

        Plan B follow-up A2: uses ``provider.complete()`` via
        ``TTS_PREP_CHAIN`` instead of inline ``anthropic.Anthropic(...)``.
        """
        text = re.sub(r"\*+", "", text)
        text = replace_roman_numerals(text)
        try:
            from .model_chains import TTS_PREP_CHAIN
            from .model_config import build_chain

            provider = build_chain(TTS_PREP_CHAIN)
            tts_system = make_tts_prep_system(self.accent, ACCENT_PROFILES, DEFAULT_ACCENT, IPA_REFERENCE)
            result, usage = provider.complete(
                system=tts_system,
                messages=[{"role": "user", "content": TTS_PREP_USER_PREFIX + text}],
                max_tokens=2048,
            )
            if self._callbacks.on_token_usage:
                self._callbacks.on_token_usage("tts_prep", provider.model, usage)
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
        _suppress_turn_start: bool = False,
        _turn_idx_override: list[int | None] | None = None,
    ) -> list:
        """
        Execute one LLM turn, pipeline TTS in parallel threads.

        For streaming providers (Anthropic), TTS synthesis starts while the LLM
        is still generating — the on_text_chunk closure accumulates a buffer and
        flushes completed lines to the TTSPipeline in real time.

        Appends the assistant message to messages.

        Returns ALL tool_use blocks from the response, in declaration order
        (the LLM may emit more than one in a single message).  Returns an
        empty list if the model returned text only or if the provider call
        errored.  Plan B run_turn iterates this list and dispatches each
        tool sequentially.
        """
        if _system is not None:
            system = _system
        else:
            system = make_teaching_prompt(
                curriculum.title, curriculum.sections, curriculum.idx, lesson_goal,
                current_tasks=curriculum.current_tasks(),
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
        # Reuse the same turn_idx across chained tool iterations so the client
        # treats all LLM calls within one run_turn() as a single visible turn.
        if _turn_idx_override is not None and _turn_idx_override[0] is not None:
            turn_idx = _turn_idx_override[0]
            turn_chunks = self._audio_turns[turn_idx]
        else:
            turn_idx = len(self._audio_turns)
            turn_chunks = []
            self._audio_turns.append(turn_chunks)
            if _turn_idx_override is not None:
                _turn_idx_override[0] = turn_idx
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
            if self._callbacks.on_turn_start and not _suppress_turn_start:
                self._callbacks.on_turn_start()

            llm_messages = self._memory_strategy(
                messages,
                turn_summaries=self._turn_tracker.recent(5),
                embedding_cache=self._embedding_cache,
            )
            # Memory strategies may produce orphaned tool_use/tool_result blocks
            # (e.g., RAG retrieves a tool_result without its preceding tool_use).
            # Strip them so the provider doesn't reject the message list.
            llm_messages = list(llm_messages)
            _strip_dangling_tool_use(llm_messages)

            _llm_t0 = time.monotonic()
            result = self._provider.do_turn(
                model=self._model,
                system=system,
                messages=llm_messages,
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
                "[llm] provider=%s model=%s call=%s tokens_in=%s tokens_out=%s cached=%s elapsed=%.2fs",
                self._provider.name,
                self._model,
                call_type,
                getattr(_usage, "input_tokens", "?"),
                getattr(_usage, "output_tokens", "?"),
                getattr(_usage, "cache_read_input_tokens", 0),
                _llm_elapsed,
            )

            messages.append({"role": "assistant", "content": result.content_blocks})

            # Distillation: log the full turn for training data collection
            if self._callbacks.on_turn_logged:
                try:
                    import copy
                    self._callbacks.on_turn_logged(
                        system,
                        copy.deepcopy(messages),
                        tools,
                        self._model,
                    )
                except Exception:
                    log.debug("on_turn_logged callback failed", exc_info=True)

        except Exception as e:
            log.exception("_do_single_llm_turn: LLM exception: %s", e)
            if self._callbacks.on_error:
                self._callbacks.on_error(str(e))
            tts.shutdown()
            return []

        _tts_t0 = time.monotonic()
        audio_chunks = tts.finish()
        self.tts_voice = tts.tts_voice  # pick up any voice updates from synthesis

        log.info("[tts] chunks=%d elapsed=%.2fs", len(audio_chunks), time.monotonic() - _tts_t0)

        all_audio = [c for c in audio_chunks if c.size > 0]
        if all_audio:
            self.last_audio = np.concatenate(all_audio)

        if self._callbacks.on_response_end:
            self._callbacks.on_response_end()

        # Plan B: return ALL tool_use blocks (the model may emit more than one
        # per response and the API requires a tool_result for each on the next
        # call).  Empty list = end-turn / no tools.
        return list(result.tool_uses) if result.tool_uses else []
