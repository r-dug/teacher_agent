"""Unit tests for TeacherAgent tool dispatch in run_turn().

Tests the tool call loop: mark_task_complete, unmark_task, auto-advance,
interactive tools (sketchpad, quiz, etc.), search_content, and edge cases.
Uses a _FakeLLMProvider that returns deterministic tool calls.

Plan B (Commit 2): tests are async because run_turn is async-native.
pytest-asyncio is configured in ``asyncio_mode = auto``, so ``async def
test_*`` functions are picked up automatically without a decorator.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from backend.services.agents.callbacks import AgentCallbacks
from backend.services.agents.curriculum import Curriculum
from backend.services.agents.providers.base import LLMProvider, LLMTurnResult
from backend.services.agents.teacher_agent import TeacherAgent
from backend.services.voice.tts import TTSSynthesisResult


def _make_async_callback(result):
    """Build an async ``on_open_interactive_tool`` fake that ignores its
    event-dict argument and resolves to ``result``.

    Used by tests that fire only one interactive tool per turn.  ``result``
    is whatever raw dict the client would have posted (or None to simulate
    dismissal).
    """
    async def _cb(_event):
        return result
    return _cb


def _make_async_routed_callback(by_event_name: dict):
    """Build an async ``on_open_interactive_tool`` fake that returns a
    different result depending on which event was sent.  Used by multi-tool
    tests that fire several interactive tools in one response.

    Keys are WS event names (e.g. ``"show_quiz"``), values are raw client
    result dicts (or None for dismissal).
    """
    async def _cb(event):
        return by_event_name.get(event.get("event"))
    return _cb


def _make_async_search(text: str | None):
    """Build an async on_search_content callback returning a fixed string."""
    async def _cb(_query, _idx):
        return text or ""
    return _cb


# ── Shared fakes ──────────────────────────────────────────────────────────────

_DEFAULT_USAGE = SimpleNamespace(
    input_tokens=10,
    output_tokens=20,
    cache_read_input_tokens=0,
    cache_creation_input_tokens=0,
)


class _SequentialLLMProvider(LLMProvider):
    """LLM provider that returns a pre-defined sequence of responses.

    Each call to do_turn() pops the next response from the queue.
    After the queue is exhausted, returns text-only (no tool) responses.

    Each entry is ``(text, tool_use_or_list)`` where the second element is
    either a single ``SimpleNamespace`` (legacy single-tool case), a list of
    them (multi-tool), or ``None`` (no tools, end-of-turn).
    """

    def __init__(self, responses: list[tuple[str, SimpleNamespace | None | list]]):
        self._queue = list(responses)
        self._call_count = 0

    @property
    def name(self) -> str:
        return "fake-sequential"

    @property
    def model(self) -> str:
        return "fake-model"

    def complete(self, system, messages, max_tokens=1024):
        # Tests don't currently exercise complete() — return a stub.
        return ("OK.", _DEFAULT_USAGE)

    def do_turn(self, model, system, messages, tools, on_text_chunk=None):
        self._call_count += 1
        if self._queue:
            text, raw_tool = self._queue.pop(0)
        else:
            text, raw_tool = ("OK.", None)

        if on_text_chunk:
            on_text_chunk(text)

        # Normalize raw_tool into a list[ToolUse]
        if raw_tool is None:
            tool_uses = []
        elif isinstance(raw_tool, list):
            tool_uses = list(raw_tool)
        else:
            tool_uses = [raw_tool]

        content_blocks: list = [{"type": "text", "text": text}]
        for tu in tool_uses:
            content_blocks.append({
                "type": "tool_use",
                "id": tu.id,
                "name": tu.name,
                "input": tu.input,
            })

        return LLMTurnResult(
            content_blocks=content_blocks,
            content_text=text,
            usage=_DEFAULT_USAGE,
            tool_uses=tool_uses,
        )


class _NoopTTS:
    """TTS provider that returns silence."""
    requires_preprocessing = False

    def synthesize(self, text: str, voice: str) -> TTSSynthesisResult:
        return TTSSynthesisResult(
            audio=np.zeros(1, dtype=np.float32),
            sample_rate=24000,
            voice=voice or "test",
            characters=len(text),
            synthesis_ms=1,
            estimated_cost_usd=0.0,
        )


def _tool(name: str, tool_id: str = "t1", **kwargs) -> SimpleNamespace:
    """Helper to build a fake tool_use object."""
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=kwargs)


def _make_agent(
    responses: list[tuple[str, SimpleNamespace | None]],
    callbacks: AgentCallbacks | None = None,
) -> TeacherAgent:
    agent = TeacherAgent(
        llm_provider=_SequentialLLMProvider(responses),
        callbacks=callbacks or AgentCallbacks(),
        tts_providers=[_NoopTTS()],
        model="test-model",
    )
    agent.prepare_for_tts = lambda text: text  # type: ignore[method-assign]
    return agent


def _make_curriculum(
    num_concepts: int = 2,
    num_sections: int = 2,
    task_progress: dict | None = None,
) -> Curriculum:
    sections = []
    for i in range(num_sections):
        concepts = [f"Concept {chr(65 + i * num_concepts + j)}" for j in range(num_concepts)]
        sections.append({
            "title": f"Section {i + 1}",
            "content": f"Content for section {i + 1}.",
            "key_concepts": concepts,
            "page_start": i * 2 + 1,
            "page_end": i * 2 + 2,
        })
    return Curriculum(
        title="Test Lesson",
        sections=sections,
        idx=0,
        task_progress=task_progress or {},
    )


# ── mark_task_complete ────────────────────────────────────────────────────────

class TestMarkTaskComplete:
    async def test_marks_concept_and_continues(self):
        """Marking a concept task succeeds and the agent continues (no turn end)."""
        task_events: list[Curriculum] = []
        turn_complete = []

        cb = AgentCallbacks(
            on_task_complete=lambda c: task_events.append(c),
            on_turn_complete=lambda audio: turn_complete.append(True),
        )
        agent = _make_agent(
            responses=[
                ("Good job!", _tool("mark_task_complete", task_idx=0, evidence="got it")),
                ("Now let's move on.", None),  # agent continues after marking
            ],
            callbacks=cb,
        )
        cur = _make_curriculum()
        messages: list[dict] = [{"role": "user", "content": "I understand concept A."}]

        await agent.run_turn(cur, messages, agent_instructions=None)

        assert len(task_events) == 1
        assert cur.current_tasks()[0]["status"] == "passed"
        assert cur.current_tasks()[1]["status"] == "pending"  # concept B still pending
        # Agent continued and ended on text-only response
        assert agent._provider._call_count == 2
        assert len(turn_complete) == 1

    async def test_quiz_auto_passes_pending_concepts(self):
        """Marking the quiz auto-passes any remaining pending concepts and advances."""
        advanced = []
        cb = AgentCallbacks(
            on_task_complete=lambda c: None,
            on_section_advanced=lambda c: advanced.append(c.idx),
            on_turn_complete=lambda audio: None,
        )
        cur = _make_curriculum(num_concepts=2, num_sections=2)
        tasks = cur.current_tasks()  # generates: [A, B, quiz]
        quiz_idx = len(tasks) - 1

        agent = _make_agent(
            responses=[
                ("You passed!", _tool("mark_task_complete", task_idx=quiz_idx, evidence="quiz ok")),
                ("Student was quick.", None),  # condensation
                ("Welcome to section 2.", None),  # new section teaching
            ],
            callbacks=cb,
        )

        messages: list[dict] = [{"role": "user", "content": "quiz me"}]
        await agent.run_turn(cur, messages, agent_instructions=None)

        # Quiz and all concepts should be passed
        assert tasks[quiz_idx]["status"] == "passed"
        assert tasks[0]["status"] == "passed"
        assert tasks[1]["status"] == "passed"
        # Should have advanced
        assert cur.idx == 1
        assert advanced == [1]

    async def test_already_passed_is_idempotent(self):
        """Marking an already-passed task succeeds idempotently and continues."""
        cb = AgentCallbacks(
            on_task_complete=lambda c: None,
            on_turn_complete=lambda audio: None,
        )
        cur = _make_curriculum(num_concepts=1)
        tasks = cur.current_tasks()
        tasks[0]["status"] = "passed"  # pre-mark concept

        agent = _make_agent(
            responses=[
                ("Already got it.", _tool("mark_task_complete", task_idx=0, evidence="redo")),
                ("Moving on.", None),
            ],
            callbacks=cb,
        )

        messages: list[dict] = [{"role": "user", "content": "I know this"}]
        await agent.run_turn(cur, messages, agent_instructions=None)

        assert tasks[0]["status"] == "passed"
        # Two LLM calls: mark (continue) + text (end turn)
        assert agent._provider._call_count == 2


# ── unmark_task ───────────────────────────────────────────────────────────────

class TestUnmarkTask:
    async def test_resets_passed_task(self):
        """unmark_task resets a passed concept back to pending."""
        task_events: list[Curriculum] = []
        cb = AgentCallbacks(
            on_task_complete=lambda c: task_events.append(c),
            on_turn_complete=lambda audio: None,
        )
        cur = _make_curriculum(num_concepts=1)
        tasks = cur.current_tasks()
        tasks[0]["status"] = "passed"
        tasks[0]["evidence"] = "earlier"

        agent = _make_agent(
            responses=[("You seem confused, let's revisit.", _tool("unmark_task", task_idx=0, reason="got confused"))],
            callbacks=cb,
        )

        messages: list[dict] = [{"role": "user", "content": "wait, what?"}]
        await agent.run_turn(cur, messages, agent_instructions=None)

        assert tasks[0]["status"] == "pending"
        assert tasks[0]["evidence"] is None
        # on_task_complete fires for unmark too (to update progress UI)
        assert len(task_events) == 1


# ── Auto-advance ──────────────────────────────────────────────────────────────

class TestAutoAdvance:
    async def test_advances_when_all_tasks_done(self):
        """If all tasks are done at the start of run_turn, auto-advance."""
        advanced: list[int] = []
        cb = AgentCallbacks(
            on_section_advanced=lambda c: advanced.append(c.idx),
            on_turn_complete=lambda audio: None,
        )
        cur = _make_curriculum(num_concepts=1, num_sections=2)
        # Mark all tasks in section 0 as passed
        for t in cur.current_tasks():
            t["status"] = "passed"

        agent = _make_agent(
            responses=[("Welcome to section two.", None)],
            callbacks=cb,
        )

        messages: list[dict] = [{"role": "user", "content": "Let's go"}]
        await agent.run_turn(cur, messages, agent_instructions=None)

        assert cur.idx == 1
        assert advanced == [1]

    async def test_curriculum_complete_on_last_section(self):
        """Auto-advance on the final section fires on_curriculum_complete."""
        completed = []
        cb = AgentCallbacks(
            on_curriculum_complete=lambda: completed.append(True),
        )
        cur = _make_curriculum(num_concepts=1, num_sections=1)
        for t in cur.current_tasks():
            t["status"] = "passed"

        agent = _make_agent(responses=[], callbacks=cb)

        messages: list[dict] = [{"role": "user", "content": "done"}]
        await agent.run_turn(cur, messages, agent_instructions=None)

        assert len(completed) == 1
        # Should return immediately, no LLM call
        assert agent._provider._call_count == 0

    async def test_no_advance_when_tasks_pending(self):
        """If tasks are still pending, no auto-advance occurs."""
        advanced: list[int] = []
        cb = AgentCallbacks(
            on_section_advanced=lambda c: advanced.append(c.idx),
            on_turn_complete=lambda audio: None,
        )
        cur = _make_curriculum(num_concepts=1, num_sections=2)
        # Leave tasks pending (default)

        agent = _make_agent(
            responses=[("Let's learn concept A.", None)],
            callbacks=cb,
        )

        messages: list[dict] = [{"role": "user", "content": "teach me"}]
        await agent.run_turn(cur, messages, agent_instructions=None)

        assert cur.idx == 0
        assert advanced == []

    async def test_advance_clears_messages_and_condenses(self):
        """Auto-advance clears messages and prepends episode summary."""
        cur = _make_curriculum(num_concepts=1, num_sections=2)
        for t in cur.current_tasks():
            t["status"] = "passed"

        # The provider will be called twice:
        # 1. _condense_episode (during auto-advance)
        # 2. The actual teaching turn for section 2
        agent = _make_agent(
            responses=[
                ("Student is quick learner.", None),  # condensation response
                ("Welcome to section two.", None),    # teaching response
            ],
            callbacks=AgentCallbacks(
                on_section_advanced=lambda c: None,
                on_turn_complete=lambda audio: None,
            ),
        )

        messages: list[dict] = [
            {"role": "user", "content": "I understand everything"},
            {"role": "assistant", "content": [{"type": "text", "text": "Great!"}]},
        ]
        await agent.run_turn(cur, messages, agent_instructions=None)

        assert cur.idx == 1
        # Messages should have been cleared and rebuilt
        # (condensation summary + new teaching turn)
        assert len(messages) >= 1


# ── Interactive tools ─────────────────────────────────────────────────────────

class TestInteractiveTools:
    async def _run_interactive_tool(
        self,
        tool_name: str,
        tool_input: dict,
        callback_name: str,  # noqa: ARG002 — kept for test readability, no longer used
        callback_result,
    ):
        """Helper: run a turn with one interactive tool and verify the flow.

        Plan B Commit 4: all 13 interactive tools share one callback,
        ``on_open_interactive_tool``, which receives a fully-built event
        dict and returns the raw client result dict (or None for dismissal).
        We register a fake async callback that ignores its argument and
        returns ``callback_result`` regardless of which tool was invoked.
        """
        cb = AgentCallbacks(
            on_turn_complete=lambda audio: None,
            on_open_interactive_tool=_make_async_callback(callback_result),
            image_gen_enabled=True,
            image_search_enabled=True,
        )

        agent = _make_agent(
            responses=[
                ("Try this exercise.", _tool(tool_name, **tool_input)),
                ("Good work!", None),  # response after tool result
            ],
            callbacks=cb,
        )

        cur = _make_curriculum()
        messages: list[dict] = [{"role": "user", "content": "teach me"}]
        await agent.run_turn(cur, messages, agent_instructions=None)

        # Should have called the LLM twice (tool call + follow-up)
        assert agent._provider._call_count == 2
        return messages

    async def test_open_sketchpad_dispatches_and_resumes(self):
        """open_sketchpad fires the callback, the agent appends the result
        and continues into a new LLM call."""
        msgs = await self._run_interactive_tool(
            tool_name="open_sketchpad",
            tool_input={"prompt": "Draw a circle"},
            callback_name="on_open_sketchpad",
            callback_result={"drawing": "base64imagedata"},
        )
        # Tool result should be in messages as a tool_result block.
        tool_results = [
            m for m in msgs
            if m.get("role") == "user"
            and isinstance(m.get("content"), list)
            and any(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in m["content"]
            )
        ]
        assert len(tool_results) >= 1

    async def test_show_quiz_correct_answer(self):
        """show_quiz fires the callback; student answered correctly."""
        await self._run_interactive_tool(
            tool_name="show_quiz",
            tool_input={
                "prompt": "What is 2+2?",
                "choices": [
                    {"label": "A", "text": "3"},
                    {"label": "B", "text": "4"},
                ],
                "correct_index": 1,
            },
            callback_name="on_show_quiz",
            callback_result={"selected_index": 1, "correct": True},
        )

    async def test_show_quiz_dismissed(self):
        """show_quiz fires the callback; student dismissed without answering."""
        await self._run_interactive_tool(
            tool_name="show_quiz",
            tool_input={
                "prompt": "What is 2+2?",
                "choices": [
                    {"label": "A", "text": "3"},
                    {"label": "B", "text": "4"},
                ],
                "correct_index": 1,
            },
            callback_name="on_show_quiz",
            callback_result=None,  # dismissed
        )

    async def test_text_input_dispatches(self):
        """text_input fires the callback and returns the student's answer."""
        await self._run_interactive_tool(
            tool_name="text_input",
            tool_input={"prompt": "Type your answer:"},
            callback_name="on_text_input",
            callback_result={"answer": "mitochondria"},
        )

    async def test_fill_in_the_blank_dispatches(self):
        """fill_in_the_blank returns student answers with correctness."""
        await self._run_interactive_tool(
            tool_name="fill_in_the_blank",
            tool_input={
                "prompt": "Complete:",
                "template": "The ___ is the powerhouse of the ___.",
                "answers": ["mitochondria", "cell"],
            },
            callback_name="on_fill_in_the_blank",
            callback_result={
                "student_answers": ["mitochondria", "cell"],
                "correct": [True, True],
            },
        )

    async def test_start_timer_dispatches(self):
        """start_timer fires the callback and returns the timed result."""
        await self._run_interactive_tool(
            tool_name="start_timer",
            tool_input={"prompt": "Quick! Name 3 vowels.", "duration_seconds": 30},
            callback_name="on_start_timer",
            callback_result={"timed_out": False, "answer": "a, e, i", "elapsed_seconds": 12},
        )

    async def test_flashcard_deck_dispatches(self):
        """show_flashcard_deck returns self-graded results."""
        await self._run_interactive_tool(
            tool_name="show_flashcard_deck",
            tool_input={
                "prompt": "Review these terms:",
                "cards": [
                    {"front": "H2O", "back": "Water"},
                    {"front": "NaCl", "back": "Salt"},
                ],
            },
            callback_name="on_show_flashcard_deck",
            callback_result={
                "results": [
                    {"card_index": 0, "self_grade": "correct"},
                    {"card_index": 1, "self_grade": "incorrect"},
                ],
            },
        )

    async def test_ordering_exercise_dispatches(self):
        """ordering_exercise returns student ordering."""
        await self._run_interactive_tool(
            tool_name="ordering_exercise",
            tool_input={
                "prompt": "Order smallest to largest:",
                "items": ["atom", "molecule", "cell"],
            },
            callback_name="on_ordering_exercise",
            callback_result={"student_order": [0, 1, 2], "correct": True},
        )

    async def test_code_editor_dispatches(self):
        """open_code_editor returns code and execution output."""
        await self._run_interactive_tool(
            tool_name="open_code_editor",
            tool_input={"prompt": "Write hello world", "language": "python"},
            callback_name="on_open_code_editor",
            callback_result={
                "code": "print('hello')",
                "stdout": "hello\n",
                "stderr": "",
                "exit_code": 0,
            },
        )

    async def test_take_photo_dispatches(self):
        """take_photo fires the callback and returns the base64 image."""
        await self._run_interactive_tool(
            tool_name="take_photo",
            tool_input={"prompt": "Show me your work."},
            callback_name="on_take_photo",
            callback_result={"photo": "base64photodata"},
        )


# ── Non-blocking tools ────────────────────────────────────────────────────────

class TestNonBlockingTools:
    async def test_search_content_injects_results_and_continues(self):
        """search_content (now async) injects its result, loop continues."""
        async def _fake_search(query: str, section_idx: int) -> str:
            return f"Found: {query} in section {section_idx}"

        cb = AgentCallbacks(
            on_search_content=_fake_search,
            on_turn_complete=lambda audio: None,
        )

        agent = _make_agent(
            responses=[
                ("Let me look that up.", _tool("search_content", query="photosynthesis")),
                ("Here's what I found.", None),
            ],
            callbacks=cb,
        )

        cur = _make_curriculum()
        messages: list[dict] = [{"role": "user", "content": "what about photosynthesis?"}]
        await agent.run_turn(cur, messages, agent_instructions=None)

        # LLM called twice: tool call + follow-up after search results
        assert agent._provider._call_count == 2
        # Search result should be in the messages
        search_results = [
            m for m in messages
            if m.get("role") == "user"
            and isinstance(m.get("content"), list)
            and any(
                isinstance(b, dict)
                and b.get("type") == "tool_result"
                and "Found: photosynthesis" in (b.get("content") or "")
                for b in m["content"]
            )
        ]
        assert len(search_results) == 1

    async def test_search_content_no_results(self):
        """search_content returns fallback when nothing found."""
        cb = AgentCallbacks(
            on_search_content=_make_async_search(""),
            on_turn_complete=lambda audio: None,
        )

        agent = _make_agent(
            responses=[
                ("Let me check.", _tool("search_content", query="obscure topic")),
                ("I couldn't find anything on that.", None),
            ],
            callbacks=cb,
        )

        cur = _make_curriculum()
        messages: list[dict] = [{"role": "user", "content": "tell me about X"}]
        await agent.run_turn(cur, messages, agent_instructions=None)

        # Should still have a tool_result with "No matching content found."
        fallback_results = [
            m for m in messages
            if m.get("role") == "user"
            and isinstance(m.get("content"), list)
            and any(
                isinstance(b, dict)
                and "No matching content" in (b.get("content") or "")
                for b in m["content"]
            )
        ]
        assert len(fallback_results) == 1

    async def test_play_audio_clip_continues_loop(self):
        """play_audio_clip is non-blocking and loops back for more."""
        played = []
        cb = AgentCallbacks(
            on_play_audio_clip=lambda text, speed: played.append(text),
            on_turn_complete=lambda audio: None,
        )

        agent = _make_agent(
            responses=[
                ("Listen to this.", _tool("play_audio_clip", text="konnichiwa", speed=0.8)),
                ("Did you hear it?", None),
            ],
            callbacks=cb,
        )

        cur = _make_curriculum()
        messages: list[dict] = [{"role": "user", "content": "how do you say hello?"}]
        await agent.run_turn(cur, messages, agent_instructions=None)

        assert played == ["konnichiwa"]
        assert agent._provider._call_count == 2

    async def test_show_progress_fires_callback(self):
        """show_progress fires the callback (non-blocking display)."""
        progress_shown = []
        cb = AgentCallbacks(
            on_show_progress=lambda c: progress_shown.append(True),
            on_turn_complete=lambda audio: None,
        )

        agent = _make_agent(
            responses=[("Here's your progress.", _tool("show_progress"))],
            callbacks=cb,
        )

        cur = _make_curriculum()
        messages: list[dict] = [{"role": "user", "content": "how am I doing?"}]
        await agent.run_turn(cur, messages, agent_instructions=None)

        assert len(progress_shown) == 1


# ── Multi-tool dispatch (Plan B Commit 3) ─────────────────────────────────────


class TestMultiToolDispatch:
    """Verify the dispatcher handles multiple tool_use blocks per LLM response.

    The Anthropic & OpenAI APIs both let the model emit more than one tool_use
    in a single message, and both require a tool_result for every one of them
    on the next call.  Plan B's run_turn iterates the full list and collects
    all tool_results into one user message.
    """

    async def test_two_pure_tools_in_one_response(self):
        """show_progress + mark_task_complete in one LLM response: both run,
        both tool_results appear in ONE user message in declaration order."""
        progress_shown: list = []
        task_events: list = []

        cb = AgentCallbacks(
            on_show_progress=lambda c: progress_shown.append(True),
            on_task_complete=lambda c: task_events.append(c),
            on_turn_complete=lambda audio: None,
        )

        # First LLM response emits two tools at once.
        agent = _make_agent(
            responses=[
                (
                    "Showing your progress and marking the task.",
                    [
                        _tool("show_progress", tool_id="t1"),
                        _tool("mark_task_complete", tool_id="t2", task_idx=0, evidence="OK"),
                    ],
                ),
                ("Now let's keep going.", None),
            ],
            callbacks=cb,
        )

        cur = _make_curriculum()
        messages: list[dict] = [{"role": "user", "content": "where are we?"}]
        await agent.run_turn(cur, messages, agent_instructions=None)

        # Both tools fired
        assert len(progress_shown) == 1
        assert len(task_events) == 1
        assert cur.current_tasks()[0]["status"] == "passed"

        # Exactly ONE user message contains BOTH tool_results, in declaration order
        tool_result_messages = [
            m for m in messages
            if m.get("role") == "user"
            and isinstance(m.get("content"), list)
            and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"])
        ]
        assert len(tool_result_messages) == 1, "expected one user message with both tool_results"
        result_blocks = [b for b in tool_result_messages[0]["content"] if b.get("type") == "tool_result"]
        assert len(result_blocks) == 2
        assert result_blocks[0]["tool_use_id"] == "t1"  # show_progress
        assert result_blocks[1]["tool_use_id"] == "t2"  # mark_task_complete

    async def test_pure_then_interactive_in_one_response(self):
        """search_content followed by show_quiz in one LLM response: pure
        tool runs immediately, interactive tool awaits the student, both
        tool_results appear in one user message."""
        cb = AgentCallbacks(
            on_search_content=_make_async_search("Found relevant content."),
            on_open_interactive_tool=_make_async_routed_callback({
                "show_quiz": {"selected_index": 0, "correct": True},
            }),
            on_turn_complete=lambda audio: None,
        )

        agent = _make_agent(
            responses=[
                (
                    "Let me look that up and quiz you on it.",
                    [
                        _tool("search_content", tool_id="t1", query="topic"),
                        _tool(
                            "show_quiz", tool_id="t2",
                            prompt="Q?",
                            choices=[{"text": "A"}, {"text": "B"}],
                            correct_index=0,
                        ),
                    ],
                ),
                ("Done.", None),
            ],
            callbacks=cb,
        )

        cur = _make_curriculum()
        messages: list[dict] = [{"role": "user", "content": "explain X"}]
        await agent.run_turn(cur, messages, agent_instructions=None)

        # One user message with both tool_results in order
        tool_result_messages = [
            m for m in messages
            if m.get("role") == "user"
            and isinstance(m.get("content"), list)
            and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"])
        ]
        assert len(tool_result_messages) == 1
        result_blocks = [b for b in tool_result_messages[0]["content"] if b.get("type") == "tool_result"]
        assert [b["tool_use_id"] for b in result_blocks] == ["t1", "t2"]
        # search_content result is the search text
        assert "Found relevant content" in result_blocks[0]["content"]
        # show_quiz result describes the student's answer
        assert "CORRECT" in result_blocks[1]["content"]

    async def test_cancellation_propagates_through_interactive_tool(self):
        """Plan B regression test for the deadlock bug.

        When run_turn is cancelled while awaiting an interactive tool, the
        CancelledError must propagate cleanly out of the awaited callback
        and unwind run_turn.  No leaked threads, no leaked futures.

        Before Plan B, ``done_event.wait()`` blocked a worker thread that
        could not be interrupted, so cancelling the awaiter only cancelled
        the asyncio task — the thread kept holding its slot forever.
        """
        import asyncio as _asyncio

        # An interactive callback that never resolves on its own.
        never_resolves_started = _asyncio.Event()

        async def _hangs_forever(_event):
            never_resolves_started.set()
            # An infinite future — only cancellation will unblock this.
            await _asyncio.Event().wait()

        cb = AgentCallbacks(
            on_open_interactive_tool=_hangs_forever,
            on_turn_complete=lambda audio: None,
        )

        agent = _make_agent(
            responses=[
                ("Try drawing this.", _tool("open_sketchpad", prompt="Draw a circle")),
                ("Nice!", None),
            ],
            callbacks=cb,
        )

        cur = _make_curriculum()
        messages: list[dict] = [{"role": "user", "content": "teach me"}]

        task = _asyncio.create_task(
            agent.run_turn(cur, messages, agent_instructions=None),
        )
        # Wait until the awaiter is suspended on the callback
        await _asyncio.wait_for(never_resolves_started.wait(), timeout=2.0)

        # Cancel and assert it unwinds within 100ms (no leaked thread/future)
        task.cancel()
        with pytest.raises(_asyncio.CancelledError):
            await _asyncio.wait_for(task, timeout=0.1)

    async def test_two_interactive_tools_in_one_response(self):
        """Two interactive tools in one response: both dispatched
        sequentially, both tool_results in one user message."""
        cb = AgentCallbacks(
            on_open_interactive_tool=_make_async_routed_callback({
                "text_input": {"answer": "first"},
                "show_quiz": {"selected_index": 1, "correct": False},
            }),
            on_turn_complete=lambda audio: None,
        )

        agent = _make_agent(
            responses=[
                (
                    "Two questions for you:",
                    [
                        _tool("text_input", tool_id="t1", prompt="Type your guess:"),
                        _tool(
                            "show_quiz", tool_id="t2",
                            prompt="Then pick:",
                            choices=[{"text": "A"}, {"text": "B"}],
                            correct_index=0,
                        ),
                    ],
                ),
                ("Thanks for both!", None),
            ],
            callbacks=cb,
        )

        cur = _make_curriculum()
        messages: list[dict] = [{"role": "user", "content": "ready"}]
        await agent.run_turn(cur, messages, agent_instructions=None)

        tool_result_messages = [
            m for m in messages
            if m.get("role") == "user"
            and isinstance(m.get("content"), list)
            and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"])
        ]
        assert len(tool_result_messages) == 1
        result_blocks = [b for b in tool_result_messages[0]["content"] if b.get("type") == "tool_result"]
        assert [b["tool_use_id"] for b in result_blocks] == ["t1", "t2"]
        assert "first" in result_blocks[0]["content"]
        assert "INCORRECT" in result_blocks[1]["content"]


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    async def test_no_tool_ends_turn(self):
        """LLM returns text only — turn ends immediately."""
        turn_complete = []
        cb = AgentCallbacks(on_turn_complete=lambda audio: turn_complete.append(True))

        agent = _make_agent(
            responses=[("Let me explain this concept.", None)],
            callbacks=cb,
        )

        cur = _make_curriculum()
        messages: list[dict] = [{"role": "user", "content": "hi"}]
        await agent.run_turn(cur, messages, agent_instructions=None)

        assert len(turn_complete) == 1
        assert agent._provider._call_count == 1

    async def test_empty_sections_raises(self):
        """run_turn raises ValueError if curriculum has no sections."""
        agent = _make_agent(responses=[])
        cur = Curriculum(title="Empty", sections=[], idx=0)
        messages: list[dict] = [{"role": "user", "content": "hi"}]

        with pytest.raises(ValueError, match="No lesson sections"):
            await agent.run_turn(cur, messages, agent_instructions=None)

    async def test_mark_task_complete_all_done_advances_immediately(self):
        """When marking the last task completes the section, advance happens
        in the same turn — the agent keeps driving."""
        cur = _make_curriculum(num_concepts=1, num_sections=2)
        tasks = cur.current_tasks()  # [concept, quiz]
        tasks[0]["status"] = "passed"  # concept already done

        task_events = []
        advanced = []
        cb = AgentCallbacks(
            on_task_complete=lambda c: task_events.append(True),
            on_section_advanced=lambda c: advanced.append(c.idx),
            on_turn_complete=lambda audio: None,
        )

        agent = _make_agent(
            responses=[
                ("You passed the quiz!", _tool("mark_task_complete", task_idx=1, evidence="quiz passed")),
                ("Student was quick.", None),  # condensation
                ("Welcome to section 2.", None),  # new section
            ],
            callbacks=cb,
        )

        messages: list[dict] = [{"role": "user", "content": "I got it"}]
        await agent.run_turn(cur, messages, agent_instructions=None)

        assert tasks[1]["status"] == "passed"
        assert len(task_events) == 1
        # Advanced immediately in the same turn
        assert cur.idx == 1
        assert advanced == [1]

    async def test_messages_initialized_when_empty(self):
        """run_turn adds a default user message if messages list is empty."""
        agent = _make_agent(
            responses=[("Hello, let's begin.", None)],
            callbacks=AgentCallbacks(on_turn_complete=lambda audio: None),
        )

        cur = _make_curriculum()
        messages: list[dict] = []
        await agent.run_turn(cur, messages, agent_instructions=None)

        # Should have added "Please begin teaching." and then the assistant response
        assert any(
            m.get("content") == "Please begin teaching."
            for m in messages
            if m.get("role") == "user"
        )


# ── Resume path (Plan B Commit 4) ─────────────────────────────────────────────


class TestResumePendingTool:
    """Verify the resume path: a previously suspended interactive tool can
    be picked up by a new session, the student's submission converted to a
    tool_result, and the conversation continued.

    The persistence layer (PG row) is exercised end-to-end by ws_session
    integration tests; these unit tests cover the in-memory dispatch path
    that doesn't require a real DB."""

    async def test_build_resumed_tool_result_sketchpad(self):
        """The static helper produces a content block identical to what
        the live dispatch path would have produced for a sketchpad
        submission."""
        from backend.services.agents.teacher_agent import TeacherAgent

        block = TeacherAgent.build_resumed_tool_result(
            tool_use_id="t-abc",
            tool_name="open_sketchpad",
            tool_input={"prompt": "Draw a triangle"},
            raw_result={"drawing": "BASE64DATA"},
        )
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "t-abc"
        # Sketchpad returns a list of content blocks (image + text)
        content = block["content"]
        assert isinstance(content, list)
        assert any(b.get("type") == "image" for b in content)
        assert any(b.get("type") == "text" for b in content)
        image_block = next(b for b in content if b.get("type") == "image")
        assert image_block["source"]["data"] == "BASE64DATA"

    async def test_build_resumed_tool_result_dismissed_quiz(self):
        """A dismissed quiz (raw is None) produces the same dismissal text
        as the live dispatch path would."""
        from backend.services.agents.teacher_agent import TeacherAgent

        block = TeacherAgent.build_resumed_tool_result(
            tool_use_id="t-q1",
            tool_name="show_quiz",
            tool_input={
                "prompt": "What is 2+2?",
                "choices": [{"text": "3"}, {"text": "4"}],
                "correct_index": 1,
            },
            raw_result=None,
        )
        assert block["type"] == "tool_result"
        assert "dismissed" in block["content"].lower()

    async def test_build_resumed_tool_result_quiz_with_input(self):
        """A quiz submission uses tool_input.choices to look up the
        student's selected text — verifies the resume path correctly
        recovers args from the persisted assistant message."""
        from backend.services.agents.teacher_agent import TeacherAgent

        block = TeacherAgent.build_resumed_tool_result(
            tool_use_id="t-q2",
            tool_name="show_quiz",
            tool_input={
                "prompt": "Pick the right one:",
                "choices": [{"text": "Apple"}, {"text": "Banana"}],
                "correct_index": 0,
            },
            raw_result={"selected_index": 0, "correct": True},
        )
        assert "Apple" in block["content"]
        assert "CORRECT" in block["content"]

    async def test_build_resumed_tool_result_unknown_tool(self):
        """Unknown tool name falls back to a generic OK / dismissal block."""
        from backend.services.agents.teacher_agent import TeacherAgent

        block = TeacherAgent.build_resumed_tool_result(
            tool_use_id="t-x",
            tool_name="not_a_real_tool",
            tool_input={},
            raw_result={"some": "data"},
        )
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "t-x"
        assert block["content"] == "OK"  # raw was truthy
