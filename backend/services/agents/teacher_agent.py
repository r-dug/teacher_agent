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
from .prompts.persona import (
    CONDENSE_EPISODE_SYSTEM,
    GENERATE_INSTRUCTIONS_SYSTEM,
    REFINE_VOICE_INSTRUCTIONS_SYSTEM,
)
from .prompts.teaching import (
    make_intro_prompt,
    make_task_status_reminder,
    make_teaching_system_prompt,
)
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


def _append_reminder_to_last_user_message(
    llm_messages: list[dict],
    reminder: str,
) -> None:
    """Append a ``<system-reminder>`` block to the trailing user message.

    Cache Plan helper.  Walks backward from the end of ``llm_messages``
    to find the most recent user-role message and appends ``reminder``
    as a text block at the end of its content.  Mutates the message list
    in place by replacing that message with a shallow copy + appended
    block (the original ``messages`` list persisted to the DB is not
    touched because ``llm_messages`` is already a separate list built
    by the memory strategy).

    Handles both string-content and list-content user messages, and
    preserves image blocks and tool_result blocks untouched.  If no
    user message exists in the list (unusual — would happen during
    an intro phase with no prior turns), the reminder is silently
    dropped.
    """
    for i in range(len(llm_messages) - 1, -1, -1):
        msg = llm_messages[i]
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        new_msg = dict(msg)
        if isinstance(content, str):
            new_msg["content"] = (content + "\n\n" + reminder) if content else reminder
        elif isinstance(content, list):
            new_msg["content"] = list(content) + [{"type": "text", "text": reminder}]
        else:
            # Unknown content shape — skip quietly.
            return
        llm_messages[i] = new_msg
        return


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
        tts_instructions: str | None = None,
        model: str = DEFAULT_LLM_MODEL,
        accent: str = DEFAULT_ACCENT,
        memory_strategy: str = "turn_summaries",
        cache_key: str | None = None,
    ) -> None:
        super().__init__(model)
        self._provider = llm_provider
        self._callbacks = callbacks
        self.tts_providers: list = list(tts_providers) if tts_providers else []
        self.tts_voice = tts_voice
        # Voice instructions for gpt-4o-mini-tts: controls speaking style
        # (tone, pace, emotion, accent).  Set per-session via the WS
        # ``set_voice_instructions`` event or from the persona's
        # ``voice_instructions`` field in the DB.
        self._tts_instructions = tts_instructions
        self._tts_speed: float | None = None
        self._tts_format: str | None = None
        self._tts_prep_prompt: str | None = None
        self.accent = accent
        # Cache Plan C2: stable per-session cache key, forwarded to the
        # OpenAI Responses API as ``prompt_cache_key``.  Ignored by the
        # Anthropic provider (no equivalent in the stable API).  Callers
        # usually pass something like ``f"enrollment-{enrollment_id}"``.
        self._cache_key = cache_key
        # Cache Plan C3: per-session cache-effectiveness counters.  The
        # per-turn log line at :655 already reports per-turn numbers;
        # these totals let AgentSession.close() emit a single summary
        # line at session teardown so we can tell at a glance whether
        # the C1 prompt restructure is actually saving tokens.
        #
        # ``_cum_tokens_in`` is the TOTAL input tokens (includes the
        # cached reads + cache creates).  Both provider normalizers
        # produce this shape; see ``_normalize_anthropic_usage`` in
        # providers/anthropic.py and the OpenAI provider's do_turn.
        self._cum_tokens_in = 0
        self._cum_tokens_cached = 0
        self._cum_tokens_cache_write = 0
        self._cum_tokens_out = 0
        self._cum_turns = 0
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

    def set_tts_instructions(self, instructions: str | None) -> None:
        self._tts_instructions = instructions or None

    def set_tts_speed(self, speed: float) -> None:
        self._tts_speed = speed

    def set_tts_format(self, fmt: str) -> None:
        self._tts_format = fmt or None

    def set_tts_prep_prompt(self, prompt: str | None) -> None:
        self._tts_prep_prompt = prompt or None

    def cache_stats(self) -> dict:
        """Cache Plan C3 + pricing consolidation: cumulative cache telemetry.

        Returns a dict with ``turns``, ``tokens_in`` (total, includes
        cached), ``tokens_cached``, ``hit_rate``, ``dollars_spent``, and
        ``dollars_saved``.  ``dollars_spent`` is the actual cost of
        every LLM turn in this session based on ``ModelSpec`` pricing.
        ``dollars_saved`` is what the caching saved vs the same calls
        with no cache hits.  When the model is unknown to
        ``lookup_pricing()``, both dollar fields report 0.0 — the token
        counts are still accurate.

        AgentSession calls this on teardown to emit a
        ``[llm-session-summary]`` log line.
        """
        from .model_config import lookup_pricing

        rate = (
            self._cum_tokens_cached / self._cum_tokens_in
            if self._cum_tokens_in > 0
            else 0.0
        )

        dollars_spent = 0.0
        dollars_saved = 0.0
        pricing = lookup_pricing(self._model)
        if pricing is not None:
            uncached_input = max(
                self._cum_tokens_in
                - self._cum_tokens_cached
                - self._cum_tokens_cache_write,
                0,
            )
            dollars_spent = pricing.cost(
                input_uncached=uncached_input,
                output_tokens=self._cum_tokens_out,
                cache_read=self._cum_tokens_cached,
                cache_write=self._cum_tokens_cache_write,
            )
            # Without caching, every token billed at cache_read or
            # cache_write rates would instead have been billed at the
            # regular input rate.  Savings = the delta.  Note: for
            # Anthropic, cache_write_per_mtok (3.75) is HIGHER than
            # input_per_mtok (3.00), so cache-creation turns produce
            # NEGATIVE savings.  That's correct — the first write
            # pays a premium for the cached copy.  The reads amortize
            # it over subsequent turns.
            dollars_saved = (
                self._cum_tokens_cached
                * (pricing.input_per_mtok - pricing.cache_read_per_mtok)
                + self._cum_tokens_cache_write
                * (pricing.input_per_mtok - pricing.cache_write_per_mtok)
            ) / 1_000_000

        return {
            "turns": self._cum_turns,
            "tokens_in": self._cum_tokens_in,
            "tokens_cached": self._cum_tokens_cached,
            "hit_rate": rate,
            "dollars_spent": dollars_spent,
            "dollars_saved": dollars_saved,
        }

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
                # _condense_episode and _refine_voice_instructions both run
                # sync LLM SDK calls; hop to threads and run in parallel so
                # voice refinement doesn't add latency to section transitions.
                _msgs_snapshot = list(messages)

                async def _run_condensation():
                    try:
                        return await asyncio.wait_for(
                            asyncio.to_thread(self._condense_episode, _msgs_snapshot, curriculum),
                            timeout=_LLM_CALL_TIMEOUT,
                        )
                    except (asyncio.TimeoutError, Exception) as exc:
                        log.warning("episode condensation failed: %s", exc)
                        return ""

                async def _run_voice_refinement():
                    try:
                        return await asyncio.wait_for(
                            asyncio.to_thread(self._refine_voice_instructions, _msgs_snapshot, curriculum),
                            timeout=_LLM_CALL_TIMEOUT,
                        )
                    except (asyncio.TimeoutError, Exception) as exc:
                        log.warning("voice instruction refinement failed: %s", exc)
                        return ""

                episode_summary, refined_voice = await asyncio.gather(
                    _run_condensation(), _run_voice_refinement(),
                )
                if refined_voice:
                    self._tts_instructions = refined_voice
                    log.info("[voice-refinement] updated: %s", refined_voice[:80])
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
    _TTS_PREP_SYSTEM_WRAPPER = (
        "You are a text preprocessor for a text-to-speech engine. "
        "Your ONLY job is to transform the input text so the TTS pronounces it correctly.\n\n"
        "HARD RULES:\n"
        "- Output ONLY the preprocessed text. Nothing else.\n"
        "- Do NOT add commentary, explanations, labels, or markdown.\n"
        "- Do NOT answer questions in the text — preprocess them as-is.\n"
        "- Do NOT change the meaning or add/remove sentences.\n"
        "- Do NOT wrap output in quotes or code blocks.\n"
        "- Preserve all punctuation and sentence structure.\n"
        "- If no changes are needed, return the input unchanged.\n\n"
        "PREPROCESSING INSTRUCTIONS:\n"
    )

    _TTS_PREP_OUTPUT_RULES = (
        "\n\nOutput the preprocessed text only. No commentary, no markdown, no labels."
    )

    def _custom_tts_prep(self, text: str, prep_prompt: str) -> str:
        """Run a custom per-persona TTS preprocessing pass."""
        original = text
        text = re.sub(r"\*+", "", text)
        try:
            from .model_chains import TTS_PREP_CHAIN
            from .model_config import build_chain

            system = self._TTS_PREP_SYSTEM_WRAPPER + prep_prompt + self._TTS_PREP_OUTPUT_RULES
            provider = build_chain(TTS_PREP_CHAIN)
            result, usage = provider.complete(
                system=system,
                messages=[{"role": "user", "content": text}],
                max_tokens=2048,
            )
            if self._callbacks.on_token_usage:
                self._callbacks.on_token_usage("tts_prep", provider.model, usage)
            transformed = result or text
            if self._callbacks.on_tts_debug:
                self._callbacks.on_tts_debug(
                    original, transformed, prep_prompt,
                    self._tts_instructions or "",
                )
            return transformed
        except Exception:
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

    def _refine_voice_instructions(self, messages: list[dict], curriculum: Curriculum) -> str:
        """Suggest refined TTS voice instructions based on the completed section.

        Runs alongside ``_condense_episode`` at section transitions.
        Observes the teaching transcript and produces 1-3 sentence
        speaking-style instructions for gpt-4o-mini-tts.  If the
        session already has voice instructions (from the persona or
        from a prior refinement), those are included in the prompt so
        the LLM can refine rather than start from scratch.
        """
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

        current_instructions_block = ""
        if self._tts_instructions:
            current_instructions_block = (
                f"\n\nCurrent voice instructions:\n\"{self._tts_instructions}\"\n"
                "Refine these based on how this session went."
            )

        result = self._provider.do_turn(
            model=self._model,
            system=REFINE_VOICE_INSTRUCTIONS_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Completed section: \"{completed_section.get('title', 'Unknown')}\"\n"
                    f"{current_instructions_block}\n\n"
                    f"Transcript:\n{chr(10).join(transcript_parts[-40:])}\n\n"
                    "Write the voice instructions."
                ),
            }],
            tools=[],
        )
        if self._callbacks.on_token_usage:
            self._callbacks.on_token_usage("voice_refinement", self._model, result.usage)
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
            task_reminder = ""
        else:
            # The persona's ``instructions`` field is either a full system
            # prompt template (with {{variable}} placeholders) or a legacy
            # identity string.  ``make_teaching_system_prompt`` handles both
            # cases and falls back to the default template when no persona
            # is set.  The volatile task checklist is injected separately
            # via ``make_task_status_reminder`` (Cache Plan C1).
            system = make_teaching_system_prompt(
                curriculum.title, curriculum.sections, curriculum.idx, lesson_goal,
                persona_template=agent_instructions,
            )
            task_reminder = make_task_status_reminder(curriculum.current_tasks())
        tools = self._effective_tools if _tools is None else _tools
        call_type = _call_type or ("intro_turn" if _tools == [] else "teach_turn")

        # Build TTSPipeline for this turn.
        # Preprocessing is enabled when the persona has a custom prep
        # prompt OR when the provider requires it (e.g. Kokoro IPA).
        # The custom prep prompt overrides the default IPA annotator.
        _prep_fn = self.prepare_for_tts
        if self._tts_prep_prompt:
            _custom_prompt = self._tts_prep_prompt
            def _prep_fn(text: str) -> str:
                return self._custom_tts_prep(text, _custom_prompt)
        tts = TTSPipeline(
            providers=self.tts_providers,
            callbacks=self._callbacks,
            preprocess_fn=_prep_fn,
            tts_voice=self.tts_voice,
            tts_instructions=self._tts_instructions,
            tts_speed=self._tts_speed,
            tts_format=self._tts_format,
            preprocess_enabled=bool(self._tts_prep_prompt),
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

            # Cache Plan: append the volatile task-status reminder to the
            # trailing user message.  The trailing user message is the new
            # turn's input (student utterance or tool_result) — it's never
            # part of any cached prefix, so appending a reminder here has
            # zero cache impact.  Everything before it (the stable system
            # prompt + full prior history) remains eligible for prefix
            # caching on both providers.
            if task_reminder and llm_messages:
                _append_reminder_to_last_user_message(llm_messages, task_reminder)

            _llm_t0 = time.monotonic()
            result = self._provider.do_turn(
                model=self._model,
                system=system,
                messages=llm_messages,
                tools=tools,
                on_text_chunk=_on_text_chunk,
                cache_key=self._cache_key,
            )
            _llm_elapsed = time.monotonic() - _llm_t0

            # Flush any remainder after the stream ends.
            if text_buf[0].strip():
                _flush_line(text_buf[0])

            if self._callbacks.on_token_usage:
                self._callbacks.on_token_usage(call_type, self._model, result.usage)

            _usage = result.usage
            _tokens_in = getattr(_usage, "input_tokens", 0) or 0
            _tokens_out = getattr(_usage, "output_tokens", 0) or 0
            _cached = getattr(_usage, "cache_read_input_tokens", 0) or 0
            _cache_write = getattr(_usage, "cache_creation_input_tokens", 0) or 0
            _hit_rate = (_cached / _tokens_in) if _tokens_in > 0 else 0.0
            log.info(
                "[llm] provider=%s model=%s call=%s tokens_in=%s tokens_out=%s cached=%s hit_rate=%.2f elapsed=%.2fs",
                self._provider.name,
                self._model,
                call_type,
                _tokens_in,
                _tokens_out,
                _cached,
                _hit_rate,
                _llm_elapsed,
            )
            # Cache Plan C3: accumulate per-session totals for the
            # summary line at teardown.
            self._cum_tokens_in += _tokens_in
            self._cum_tokens_cached += _cached
            self._cum_tokens_cache_write += _cache_write
            self._cum_tokens_out += _tokens_out
            self._cum_turns += 1

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
                        voice_instructions=self._tts_instructions,
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
