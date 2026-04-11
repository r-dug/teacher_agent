"""AgentCallbacks — groups all event hooks passed to an LLM-powered agent.

Plan B Commit 5 renames the old ``TeachingCallbacks`` to ``AgentCallbacks``;
``TeachingCallbacks`` is preserved as an alias for backwards compatibility.
The new name reflects that this dataclass is suitable for any LLM-powered
agent (not just the teaching agent) — the same shape would serve a planner
agent or any future agent that uses tools and emits streaming output.

Plan B (Commit 2+): interactive tool callbacks are async and the agent
``await``s them directly.  Internally each one creates a future registered
in ``AgentSession._tool_futures``, sends the open_* event, and awaits the
future for the student's submission.  No threading primitives — ``run_turn``
is async-native and the cross-thread blocking deadlock is gone.

On WS disconnect the parent task is cancelled, which propagates
``CancelledError`` through the awaited callback; ``cancel_pending_tools``
also cancels every registered future.

Pure (non-blocking) callbacks (``on_show_progress``, ``on_play_audio_clip``,
``on_search_content`` is async), lifecycle hooks, and streaming callbacks
(``on_text_chunk``, ``on_audio_chunk``) are documented per-field below.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import numpy as np

from .curriculum import Curriculum


# Type alias: an interactive callback is an async function that resolves to
# the raw dict the client posted as a tool_result (or to None on dismiss).
InteractiveCallback = Callable[..., Awaitable[dict | None]]


@dataclass
class AgentCallbacks:
    """All optional event callbacks for an LLM-powered agent session.

    Pass an instance to TeacherAgent (or any future agent) instead of 20+
    individual keyword args.  Every field defaults to None (no-op).

    Plan B Commit 5: renamed from ``TeachingCallbacks``.  The old name is
    kept as an alias below for backwards compatibility.
    """

    # ── Lifecycle ─────────────────────────────────────────────────────────
    on_status: Callable[[str], None] | None = None
    on_turn_start: Callable[[], None] | None = None
    on_text_chunk: Callable[[str], None] | None = None
    on_chunk_ready: Callable[[str, int, int], None] | None = None
    on_audio_chunk: Callable[[np.ndarray, int, int], None] | None = None

    # ── Interactive tool callback (Plan B Commit 4: unified) ──────────────
    # ONE async callback for all 11 client-interactive tools.  The
    # dispatcher passes a fully-built ``event`` dict (containing
    # ``invocation_id``, the tool-specific payload, and the WS event name);
    # the session registers a future, sends the event to the client, awaits
    # the student's submission, and returns the raw client result dict (or
    # None if cancelled / dismissed).
    #
    # Per-tool event construction lives in ``tool_registry.py``'s
    # ``ToolSpec.build_event`` lambdas, NOT in this dataclass.
    on_open_interactive_tool: InteractiveCallback | None = None

    # ── Server-side image tools (no client-side wait) ─────────────────────
    # ``generate_visual_aid`` and ``search_image`` are conceptually
    # interactive (the LLM emits a tool call, the client sees the result),
    # but the *work* is server-side: the session calls the image provider /
    # search API and pushes a ``show_image`` event to the client.  No
    # tool_result is awaited from the client; the agent gets back a dict
    # ``{"image_url": str}`` on success or None on failure.
    on_generate_visual_aid: Callable[[str, str, str], Awaitable[dict | None]] | None = None
    on_search_image: Callable[[str, str, str], Awaitable[dict | None]] | None = None

    # Capability flags — used by ``TeacherAgent._effective_tools`` to gate
    # which tools the LLM is told about.  ``generate_visual_aid`` and
    # ``search_image`` are only added to the tool schema when the session
    # has the corresponding provider configured (image gen / image search).
    image_gen_enabled: bool = False
    image_search_enabled: bool = False

    # ── Pure (non-blocking) callbacks ─────────────────────────────────────
    # RAG: (query, current_section_idx) -> formatted results string. Async
    # because the dispatcher is async and the body does an async DB query.
    on_search_content: Callable[[str, int], Awaitable[str]] | None = None
    on_show_progress: Callable[..., None] | None = None
    on_play_audio_clip: Callable[[str, float], None] | None = None

    # Plan B: called by the dispatcher right before it awaits an interactive
    # tool callback.  The session uses this to record (tool_use_id, turn_id)
    # so the persisted pending_tool_invocations row carries them — needed
    # for resume across reconnect / device switch (decision 5 of Plan B).
    # Sync, fire-and-forget; the session just stashes the values.
    on_dispatch_context: Callable[[str, str], None] | None = None

    # ── Telemetry / lifecycle hooks ───────────────────────────────────────
    on_token_usage: Callable[[str, str, object], None] | None = None
    on_task_complete: Callable[[Curriculum], None] | None = None
    on_section_advanced: Callable[[Curriculum], None] | None = None
    on_curriculum_complete: Callable[[], None] | None = None
    on_turn_complete: Callable[[np.ndarray | None], None] | None = None
    on_response_end: Callable[[], None] | None = None
    on_tts_playing: Callable[[bool], None] | None = None
    on_tts_done: Callable[..., None] | None = None
    on_error: Callable[[str], None] | None = None
    # Distillation: (system_prompt, messages_snapshot, tools, model)
    on_turn_logged: Callable[[str, list[dict], list[dict], str], None] | None = None


# Backwards-compat alias.  Plan B Commit 5 renamed the canonical class to
# ``AgentCallbacks`` (since this dataclass is suitable for any LLM-powered
# agent, not just the teaching agent), but every existing import in the
# codebase still uses ``TeachingCallbacks``.  Keep the alias indefinitely;
# call sites can migrate at their own pace.
TeachingCallbacks = AgentCallbacks
