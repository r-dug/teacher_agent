"""
Unified tool dispatch table for the teaching agent.

Plan B Commit 4: replaces the 13 inline interactive-tool blocks plus the
if/elif chain for pure & curriculum tools in ``teacher_agent._dispatch_one_tool``
with one declarative registry.

Each tool the agent can call (``mark_task_complete``, ``open_sketchpad``,
``show_quiz``, ``search_content``, …) is described by a ``ToolSpec``.  The
dispatcher iterates the LLM's ``tool_use`` blocks, looks up each spec, and
``await``s the spec's handler.

Three kinds of tool, all sharing one async handler signature:

  - ``pure``        — never suspends; e.g. ``search_content`` (async DB query),
                      ``show_progress`` / ``play_audio_clip`` (sync side-effect).
                      Handler is ``async def`` purely for uniformity.
  - ``interactive`` — opens a UI on the client and ``await``s a future the
                      WS receive loop resolves when the student submits.  No
                      server-side timeout (Plan B decision 1): the student
                      can leave the browser open for hours.  After every
                      interactive dispatch the visible message bubble is
                      reset, so the next LLM text starts a new bubble (Plan
                      B decision 1a) — handled by the dispatcher.
  - ``curriculum``  — mutates the shared ``Curriculum`` object in place
                      (mark_task_complete, unmark_task) and returns a
                      tool_result content block.

For interactive tools, the spec's ``build_event`` lambda constructs the
client-facing WS payload from the LLM's ``tool.input``, and the spec's
``build_result`` lambda converts the raw client submission back into a
content block for the next LLM call.  Both are also used by the **resume
path** when a previously persisted interactive tool wait is replayed across
WS reconnect / device switch — see ``AgentSession.consume_resumed_tool``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .curriculum import Curriculum
    from .teacher_agent import TeacherAgent

log = logging.getLogger(__name__)


ToolKind = Literal["pure", "interactive", "curriculum"]

# Handler signature: async (agent, tool_use, curriculum) -> tool_result block
ToolHandler = Callable[
    ["TeacherAgent", SimpleNamespace, "Curriculum"],
    Awaitable[dict],
]

# build_event: convert tool.input → WS event payload (interactive tools).
# Receives ``(tool_input: dict, invocation_id: str)`` and returns the dict
# the session will send to the client.
EventBuilder = Callable[[dict, str], dict]

# build_result: convert (raw_client_dict, tool_input) → tool_result content
# (a string or list of content blocks).  Used by the interactive handler
# AND by the resume path so the same logic produces the same output.
ResultBuilder = Callable[[Any, dict], list | str]


@dataclass(frozen=True)
class ToolSpec:
    """Declarative description of one tool the agent can call."""

    name: str
    kind: ToolKind
    handler: ToolHandler
    # Only set for interactive tools — exposed so the resume path can use
    # the same builder as the live dispatch path.
    build_result: ResultBuilder | None = None


# ── Registry ─────────────────────────────────────────────────────────────────
TOOL_REGISTRY: dict[str, ToolSpec] = {}


def _register(spec: ToolSpec) -> ToolSpec:
    """Register *spec* and return it (so call sites can chain)."""
    if spec.name in TOOL_REGISTRY:
        raise ValueError(f"duplicate ToolSpec for {spec.name!r}")
    TOOL_REGISTRY[spec.name] = spec
    return spec


# ── Interactive tool factory ─────────────────────────────────────────────────


def make_interactive_spec(
    *,
    name: str,
    build_event: EventBuilder,
    build_result: ResultBuilder,
) -> ToolSpec:
    """Build a ToolSpec for one of the 13 interactive tools.

    The returned handler:
      1. Generates an ``invocation_id``
      2. Builds the WS event payload via ``build_event``
      3. Tells the session about ``(tool_use_id, turn_id)`` for persistence
      4. Calls ``agent._callbacks.on_open_interactive_tool(event)`` and
         awaits the student's submission
      5. Returns a tool_result block built by ``build_result(raw, tool.input)``
    """

    async def _handler(agent, tool, curriculum):  # noqa: ARG001 — curriculum unused
        # Tell the session which tool_use we're about to dispatch so it can
        # persist the pending row to PG (Plan B decision 5).
        if agent._callbacks.on_dispatch_context:
            agent._callbacks.on_dispatch_context(tool.id, agent._current_turn_id)

        inv_id = str(uuid.uuid4())
        event = build_event(tool.input or {}, inv_id)
        # build_event is responsible for stamping invocation_id; double-check.
        event.setdefault("invocation_id", inv_id)

        raw = None
        callback = agent._callbacks.on_open_interactive_tool
        if callback is not None:
            raw = await callback(event)

        return {
            "type": "tool_result",
            "tool_use_id": tool.id,
            "content": build_result(raw, tool.input or {}),
        }

    return ToolSpec(name=name, kind="interactive", handler=_handler, build_result=build_result)


# ── Helper: extract a single value from a raw client result dict ─────────────


def _raw_get(raw, key: str):
    """Return ``raw[key]`` if raw is a dict, else None.  Convenience for
    build_result lambdas that need to handle ``raw is None`` (cancel /
    dismiss) uniformly."""
    if isinstance(raw, dict):
        return raw.get(key)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Curriculum tools
# ─────────────────────────────────────────────────────────────────────────────


async def _handle_mark_task_complete(agent, tool, curriculum):
    task_idx = int((tool.input or {}).get("task_idx", -1))
    evidence = (tool.input or {}).get("evidence", "")
    result = curriculum.mark_task(task_idx, evidence)
    if result:
        log.info(
            "mark_task_complete OK: task_idx=%d concept=%s all_done=%s section=%d",
            task_idx, result["concept"][:60], curriculum.all_tasks_done(), curriculum.idx,
        )
        if agent._callbacks.on_task_complete:
            agent._callbacks.on_task_complete(curriculum)
        return {"type": "tool_result", "tool_use_id": tool.id, "content": "OK"}

    tasks = curriculum.current_tasks()
    statuses = [(i, t["status"]) for i, t in enumerate(tasks)]
    log.warning(
        "mark_task_complete REJECTED: task_idx=%d statuses=%s section=%d",
        task_idx, statuses, curriculum.idx,
    )
    pending = [f"{i}. {t['concept'][:50]}" for i, t in enumerate(tasks) if t["status"] == "pending"]
    return {
        "type": "tool_result",
        "tool_use_id": tool.id,
        "content": (
            f"Task {task_idx} could not be marked complete "
            "(already done, invalid index, or quiz attempted before "
            "all concepts are passed). "
            f"Still pending: {'; '.join(pending) if pending else 'none'}. "
            "Remember: the LAST task is the section quiz — you must call "
            "mark_task_complete for it too once the student passes. "
            "Do NOT repeat yourself to the student."
        ),
        "is_error": True,
    }


async def _handle_unmark_task(agent, tool, curriculum):
    task_idx = int((tool.input or {}).get("task_idx", -1))
    reason = (tool.input or {}).get("reason", "")
    result = curriculum.unmark_task(task_idx, reason)
    log.info(
        "unmark_task: task_idx=%d reason=%s result=%s section=%d",
        task_idx, reason[:80], "OK" if result else "REJECTED", curriculum.idx,
    )
    if result and agent._callbacks.on_task_complete:
        agent._callbacks.on_task_complete(curriculum)
    return {"type": "tool_result", "tool_use_id": tool.id, "content": "OK"}


_register(ToolSpec(name="mark_task_complete", kind="curriculum", handler=_handle_mark_task_complete))
_register(ToolSpec(name="unmark_task", kind="curriculum", handler=_handle_unmark_task))


# ─────────────────────────────────────────────────────────────────────────────
# Pure tools (no result blocking, no UI)
# ─────────────────────────────────────────────────────────────────────────────


async def _handle_search_content(agent, tool, curriculum):
    query = (tool.input or {}).get("query", "")
    results_text = ""
    if agent._callbacks.on_search_content:
        results_text = await agent._callbacks.on_search_content(query, curriculum.idx)
    return {
        "type": "tool_result",
        "tool_use_id": tool.id,
        "content": results_text or "No matching content found.",
    }


async def _handle_show_progress(agent, tool, curriculum):
    if agent._callbacks.on_show_progress:
        agent._callbacks.on_show_progress(curriculum)
    return {"type": "tool_result", "tool_use_id": tool.id, "content": "OK"}


async def _handle_play_audio_clip(agent, tool, curriculum):  # noqa: ARG001
    text = (tool.input or {}).get("text", "")
    speed = float((tool.input or {}).get("speed", 1.0))
    if agent._callbacks.on_play_audio_clip:
        agent._callbacks.on_play_audio_clip(text, speed)
    return {
        "type": "tool_result",
        "tool_use_id": tool.id,
        "content": "Audio clip played for the student.",
    }


_register(ToolSpec(name="search_content", kind="pure", handler=_handle_search_content))
_register(ToolSpec(name="show_progress", kind="pure", handler=_handle_show_progress))
_register(ToolSpec(name="play_audio_clip", kind="pure", handler=_handle_play_audio_clip))


# ─────────────────────────────────────────────────────────────────────────────
# Interactive tools (await student input via the unified callback)
# ─────────────────────────────────────────────────────────────────────────────


# ── open_sketchpad ──
def _sketchpad_event(tool_input, inv_id):
    event = {
        "event": "open_sketchpad",
        "prompt": tool_input.get("prompt", "Please draw:"),
        "invocation_id": inv_id,
    }
    if tool_input.get("text_bg"):
        event["text_bg"] = tool_input["text_bg"]
    if tool_input.get("bg_image_url"):
        event["im_bg"] = tool_input["bg_image_url"]
    if tool_input.get("duration_seconds"):
        event["duration_seconds"] = tool_input["duration_seconds"]
    return event


def _sketchpad_result(raw, tool_input):  # noqa: ARG001
    drawing = _raw_get(raw, "drawing")
    if not drawing:
        return "The student dismissed the drawing tool without submitting."
    return [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": drawing}},
        {"type": "text", "text": "The student has submitted their drawing. Please evaluate it and give feedback."},
    ]


_register(make_interactive_spec(
    name="open_sketchpad",
    build_event=_sketchpad_event,
    build_result=_sketchpad_result,
))


# ── take_photo ──
def _take_photo_event(tool_input, inv_id):
    return {
        "event": "take_photo",
        "prompt": tool_input.get("prompt", "Please take a photo."),
        "invocation_id": inv_id,
    }


def _take_photo_result(raw, tool_input):  # noqa: ARG001
    photo = _raw_get(raw, "photo")
    if not photo:
        return "The student dismissed the photo tool without taking a photo."
    return [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": photo}},
        {"type": "text", "text": "The student has taken a photo. Please observe and respond."},
    ]


_register(make_interactive_spec(
    name="take_photo",
    build_event=_take_photo_event,
    build_result=_take_photo_result,
))


# ── record_video ──
def _record_video_event(tool_input, inv_id):
    return {
        "event": "record_video",
        "prompt": tool_input.get("prompt", "Please record a short video."),
        "invocation_id": inv_id,
    }


def _record_video_result(raw, tool_input):  # noqa: ARG001
    frames = _raw_get(raw, "video_frames") or []
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
    return frame_blocks


_register(make_interactive_spec(
    name="record_video",
    build_event=_record_video_event,
    build_result=_record_video_result,
))


# ── open_code_editor ──
def _code_editor_event(tool_input, inv_id):
    event = {
        "event": "open_code_editor",
        "prompt": tool_input.get("prompt", "Complete the coding challenge."),
        "language": tool_input.get("language", "python"),
        "invocation_id": inv_id,
    }
    if tool_input.get("starter_code") is not None:
        event["starter_code"] = tool_input["starter_code"]
    if tool_input.get("duration_seconds"):
        event["duration_seconds"] = tool_input["duration_seconds"]
    return event


def _code_editor_result(raw, tool_input):
    language = (tool_input or {}).get("language", "python")
    r = raw or {}
    return (
        f"The student submitted the following {language} code:\n\n"
        f"```{language}\n{r.get('code', '')}\n```\n\n"
        f"Execution output:\n"
        f"stdout: {r.get('stdout', '') or '(none)'}\n"
        f"stderr: {r.get('stderr', '') or '(none)'}\n"
        f"exit code: {r.get('exit_code', -1)}"
    )


_register(make_interactive_spec(
    name="open_code_editor",
    build_event=_code_editor_event,
    build_result=_code_editor_result,
))


# ── open_html_editor ──
def _html_editor_event(tool_input, inv_id):
    event = {
        "event": "open_html_editor",
        "prompt": tool_input.get("prompt", "Complete the HTML/CSS challenge."),
        "invocation_id": inv_id,
    }
    if tool_input.get("starter_html") is not None:
        event["starter_html"] = tool_input["starter_html"]
    if tool_input.get("starter_css") is not None:
        event["starter_css"] = tool_input["starter_css"]
    if tool_input.get("duration_seconds"):
        event["duration_seconds"] = tool_input["duration_seconds"]
    return event


def _html_editor_result(raw, tool_input):  # noqa: ARG001
    r = raw or {}
    return (
        f"The student submitted the following HTML/CSS:\n\n"
        f"HTML:\n```html\n{r.get('html', '')}\n```\n\n"
        f"CSS:\n```css\n{r.get('css', '')}\n```"
    )


_register(make_interactive_spec(
    name="open_html_editor",
    build_event=_html_editor_event,
    build_result=_html_editor_result,
))


# ── start_timer ──
def _timer_event(tool_input, inv_id):
    return {
        "event": "start_timer",
        "prompt": tool_input.get("prompt", "Complete the exercise."),
        "duration_seconds": int(tool_input.get("duration_seconds", 60)),
        "invocation_id": inv_id,
    }


def _timer_result(raw, tool_input):  # noqa: ARG001
    r = raw or {}
    timed_out = r.get("timed_out", True)
    answer = (r.get("answer") or "").strip()
    elapsed = r.get("elapsed_seconds")
    if timed_out:
        return (
            f"Time expired. The student's answer: {answer}"
            if answer else "Time expired. The student did not submit an answer."
        )
    time_str = f"{elapsed}s" if elapsed is not None else "early"
    return (
        f"The student submitted early ({time_str}). Answer: {answer}"
        if answer else f"The student submitted early ({time_str}) without writing an answer."
    )


_register(make_interactive_spec(
    name="start_timer",
    build_event=_timer_event,
    build_result=_timer_result,
))


# ── generate_visual_aid (server-side, no client wait) ──
# The session does the work via on_generate_visual_aid: calls the image
# provider, persists the bytes, sends the show_image event to the client,
# and returns ``{"image_url": str}`` on success or None on failure.  This
# is NOT a client-interactive tool — there's no tool_result waited for from
# the client.  Classified as ``pure`` so the dispatcher doesn't end the
# visible bubble (the image renders inline with the teacher's narration).
async def _handle_generate_visual_aid(agent, tool, curriculum):  # noqa: ARG001
    raw = None
    if agent._callbacks.on_generate_visual_aid:
        raw = await agent._callbacks.on_generate_visual_aid(
            (tool.input or {}).get("prompt", ""),
            (tool.input or {}).get("caption", ""),
            tool.id,
        )
    image_url = _raw_get(raw, "image_url")
    if image_url:
        content = "Image generated successfully and is now displayed to the student."
    else:
        content = (
            "Image generation failed. Continue teaching without a visual aid. "
            "Let the student know briefly that the image could not be generated."
        )
    return {"type": "tool_result", "tool_use_id": tool.id, "content": content}


_register(ToolSpec(
    name="generate_visual_aid",
    kind="pure",
    handler=_handle_generate_visual_aid,
))


# ── search_image (server-side, no client wait) ──
async def _handle_search_image(agent, tool, curriculum):  # noqa: ARG001
    raw = None
    if agent._callbacks.on_search_image:
        raw = await agent._callbacks.on_search_image(
            (tool.input or {}).get("query", ""),
            (tool.input or {}).get("caption", ""),
            tool.id,
        )
    image_url = _raw_get(raw, "image_url")
    if image_url:
        content = "Image found and displayed to the student."
    else:
        content = (
            "Image search returned no results. Continue teaching without a visual. "
            "Let the student know briefly that no image was found."
        )
    return {"type": "tool_result", "tool_use_id": tool.id, "content": content}


_register(ToolSpec(
    name="search_image",
    kind="pure",
    handler=_handle_search_image,
))


# ── text_input ──
def _text_input_event(tool_input, inv_id):
    event = {
        "event": "text_input",
        "prompt": tool_input.get("prompt", "Type your answer:"),
        "invocation_id": inv_id,
    }
    if tool_input.get("placeholder"):
        event["placeholder"] = tool_input["placeholder"]
    if tool_input.get("multiline"):
        event["multiline"] = bool(tool_input["multiline"])
    if tool_input.get("duration_seconds"):
        event["duration_seconds"] = tool_input["duration_seconds"]
    return event


def _text_input_result(raw, tool_input):  # noqa: ARG001
    r = raw or {}
    answer = r.get("answer", "").strip() if isinstance(r, dict) else ""
    return (
        f"The student's written answer: {answer}"
        if answer else "The student skipped without answering."
    )


_register(make_interactive_spec(
    name="text_input",
    build_event=_text_input_event,
    build_result=_text_input_result,
))


# ── show_quiz ──
def _quiz_event(tool_input, inv_id):
    event = {
        "event": "show_quiz",
        "prompt": tool_input.get("prompt", ""),
        "choices": tool_input.get("choices", []),
        "correct_index": int(tool_input.get("correct_index", 0)),
        "invocation_id": inv_id,
    }
    if tool_input.get("duration_seconds"):
        event["duration_seconds"] = tool_input["duration_seconds"]
    return event


def _quiz_result(raw, tool_input):
    choices = (tool_input or {}).get("choices", [])
    correct_index = int((tool_input or {}).get("correct_index", 0))
    r = raw or {}
    selected = r.get("selected_index", -1) if isinstance(r, dict) else -1
    correct = r.get("correct", False) if isinstance(r, dict) else False
    if selected == -1:
        return "The student dismissed the quiz without answering."
    choice_text = choices[selected]["text"] if 0 <= selected < len(choices) else "unknown"
    correct_text = choices[correct_index]["text"] if 0 <= correct_index < len(choices) else "unknown"
    return (
        f"The student selected: {choice_text} — {'CORRECT' if correct else 'INCORRECT'}. "
        f"(Correct answer was: {correct_text})"
    )


_register(make_interactive_spec(
    name="show_quiz",
    build_event=_quiz_event,
    build_result=_quiz_result,
))


# ── fill_in_the_blank ──
def _fill_blank_event(tool_input, inv_id):
    event = {
        "event": "fill_in_the_blank",
        "prompt": tool_input.get("prompt", ""),
        "template": tool_input.get("template", ""),
        "answers": tool_input.get("answers", []),
        "invocation_id": inv_id,
    }
    if tool_input.get("duration_seconds"):
        event["duration_seconds"] = tool_input["duration_seconds"]
    return event


def _fill_blank_result(raw, tool_input):
    answers = (tool_input or {}).get("answers", [])
    r = raw or {}
    student_answers = r.get("student_answers", []) if isinstance(r, dict) else []
    correct_flags = r.get("correct", []) if isinstance(r, dict) else []
    if not student_answers:
        return "The student skipped the fill-in-the-blank exercise."
    pairs = []
    for i, (sa, ans) in enumerate(zip(student_answers, answers)):
        is_correct = correct_flags[i] if i < len(correct_flags) else False
        pairs.append(
            f"Blank {i + 1}: '{sa}' "
            f"({'correct' if is_correct else f'incorrect, expected: {ans}'})"
        )
    return "Fill-in-the-blank results:\n" + "\n".join(pairs)


_register(make_interactive_spec(
    name="fill_in_the_blank",
    build_event=_fill_blank_event,
    build_result=_fill_blank_result,
))


# ── show_flashcard_deck ──
def _flashcard_event(tool_input, inv_id):
    event = {
        "event": "show_flashcard_deck",
        "prompt": tool_input.get("prompt", ""),
        "cards": tool_input.get("cards", []),
        "invocation_id": inv_id,
    }
    if tool_input.get("duration_seconds"):
        event["duration_seconds"] = tool_input["duration_seconds"]
    return event


def _flashcard_result(raw, tool_input):
    cards = (tool_input or {}).get("cards", [])
    r = raw or {}
    results = r.get("results", []) if isinstance(r, dict) else []
    if not results:
        return "The student dismissed the flashcards without completing them."
    correct_count = sum(1 for cr in results if cr.get("self_grade") == "correct")
    total = len(results)
    missed = [
        cards[cr["card_index"]]["front"]
        for cr in results
        if cr.get("self_grade") == "incorrect" and 0 <= cr.get("card_index", -1) < len(cards)
    ]
    content = f"Flashcard results: {correct_count}/{total} correct."
    if missed:
        content += f" Missed: {', '.join(missed)}"
    return content


_register(make_interactive_spec(
    name="show_flashcard_deck",
    build_event=_flashcard_event,
    build_result=_flashcard_result,
))


# ── ordering_exercise ──
# Note: the original session-side callback shuffled items + sent
# correct_order (the indices that map shuffled position → original).  The
# registry now does the shuffle here in build_event.
def _ordering_event(tool_input, inv_id):
    import random as _random
    items = tool_input.get("items", [])
    indices = list(range(len(items)))
    _random.shuffle(indices)
    shuffled_items = [items[i] for i in indices]
    event = {
        "event": "ordering_exercise",
        "prompt": tool_input.get("prompt", ""),
        "items": shuffled_items,
        "correct_order": indices,
        "invocation_id": inv_id,
    }
    if tool_input.get("duration_seconds"):
        event["duration_seconds"] = tool_input["duration_seconds"]
    return event


def _ordering_result(raw, tool_input):  # noqa: ARG001
    r = raw or {}
    student_order = r.get("student_order", []) if isinstance(r, dict) else []
    is_correct = r.get("correct", False) if isinstance(r, dict) else False
    if not student_order:
        return "The student dismissed the ordering exercise."
    content = f"The student's ordering was {'CORRECT' if is_correct else 'INCORRECT'}."
    if not is_correct:
        content += (
            f" Their order: {', '.join(str(i) for i in student_order)}. "
            "Correct order: 0, 1, 2, ..."
        )
    return content


_register(make_interactive_spec(
    name="ordering_exercise",
    build_event=_ordering_event,
    build_result=_ordering_result,
))


# ── Set of interactive tool names (for use by the dispatcher) ────────────────
INTERACTIVE_TOOL_NAMES = frozenset(
    name for name, spec in TOOL_REGISTRY.items() if spec.kind == "interactive"
)
