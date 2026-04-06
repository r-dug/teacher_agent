# Agents

The teaching agent subsystem. Orchestrates LLM-driven lessons with voice, tools, and structured mastery tracking.

## Architecture

```
session.py (AgentSession)
    |
    |--- teacher_agent.py (TeacherAgent)
    |       |--- providers/    LLM providers (Anthropic, OpenAI, fallback chain)
    |       |--- prompts/      System prompt generators
    |       |--- tools.py      Tool schemas (advance, mark_task, sketchpad, etc.)
    |       |--- tts_pipeline.py   TTS with ordered fallback
    |
    |--- planner_agent.py      PDF decomposition into curriculum
    |--- code_runner.py        Sandboxed code execution for student exercises
    |--- callbacks.py          Event hooks (section advanced, task complete, etc.)
    |--- curriculum.py         Curriculum dataclass with task checklist
```

## Session Lifecycle

1. **`AgentSession`** is created per WebSocket connection (`session.py`). It wires up callbacks, constructs the LLM provider chain, and owns the `TeacherAgent`.

2. **Intro phase** — `run_intro_turn()` uses `capture_lesson_goal` to understand what the student wants to learn. Goal is stored on the enrollment.

3. **Teaching loop** — `run_turn()` calls `TeacherAgent.run_turn()` in a background thread. The agent loops: call LLM, route tool results, repeat until no more tool calls.

4. **Turn completion** — callbacks fire WS events back to the client. `_save_state` persists curriculum index, task progress, and messages to PostgreSQL.

## Teaching Agent Turn Flow

```
while True:
    tool = _do_single_llm_turn(curriculum, messages)
    if tool is None: return               # no tool call, turn done

    if tool == "mark_task_complete":       # mark a concept as understood
        curriculum.mark_task(idx, evidence)
        fire on_task_complete callback
        continue

    if tool == "advance_to_next_section":  # gated: all tasks must be done
        if not all_tasks_done: return error, continue
        increment curriculum.idx
        condense episode into student profile
        clear messages, start fresh
        continue

    if tool in INTERACTIVE_TOOLS:          # sketchpad, code editor, etc.
        send tool event to client via WS
        wait for student response
        append result to messages
        continue
```

## Task Checklist (Per-Section Mastery Tracking)

Each section has `key_concepts` (defined during PDF decomposition). When a section is entered, these are auto-generated into a task checklist:

```json
{
  "0": [
    {"concept": "Newton's first law", "status": "passed", "evidence": "..."},
    {"concept": "Inertia", "status": "pending", "evidence": null}
  ]
}
```

- The agent sees the checklist (with `[DONE]`/`[PENDING]` tags) in its system prompt every turn
- It calls `mark_task_complete(task_idx, evidence)` as the student demonstrates understanding
- `advance_to_next_section` is gated — returns an error if any tasks are still pending
- Task progress is persisted to `lesson_enrollments.task_progress` (JSON) after each turn
- On reconnect, progress is restored from DB

## LLM Provider Chain

```
providers/
  base.py          Abstract LLMProvider interface
  anthropic.py     Anthropic (Claude) — primary
  openai.py        OpenAI (GPT) — fallback
  fallback.py      FallbackLLMProvider wraps [(provider, model), ...] pairs
```

`FallbackLLMProvider` tries each provider in sequence. On failure, advances to the next permanently for that turn. Chain construction happens in `session.py`.

## Tools

Defined in `tools.py` as plain dicts (Anthropic tool schema format):

| Tool | Purpose | Affects Progress |
|------|---------|:---:|
| `mark_task_complete` | Mark a concept as understood | Yes |
| `advance_to_next_section` | Move to next section (gated) | Yes |
| `mark_curriculum_complete` | End the lesson | Yes |
| `show_slide` | Display PDF pages | No |
| `open_sketchpad` | Drawing canvas (characters, diagrams) | No |
| `take_photo` | Camera capture for physical work | No |
| `record_video` | Video capture (ASL, techniques) | No |
| `open_code_editor` | Code editor with execution | No |
| `open_html_editor` | HTML/CSS editor with live preview | No |
| `start_timer` | Timed exercises | No |
| `generate_visual_aid` | AI image generation (DALL-E) | No |
| `search_image` | Web image search | No |

## Prompts

```
prompts/
  teaching.py     make_teaching_prompt() — main teaching system prompt
  decompose.py    PDF decomposition prompts
  persona.py      Episode condensation, instruction generation
  tts_prep.py     TTS preprocessing (number spelling, abbreviation expansion)
  search.py       Web search prompts (used during decomposition)
```

The teaching prompt is rebuilt every LLM call with current section content, task checklist statuses, and optional lesson goal.

## Callbacks

`TeachingCallbacks` (`callbacks.py`) groups all event hooks as optional callables. Key ones:

| Callback | Fired When |
|----------|------------|
| `on_task_complete` | Agent marks a concept task as understood |
| `on_section_advanced` | Agent advances to next section |
| `on_curriculum_complete` | Lesson finished |
| `on_text_chunk` | LLM streams a text chunk (for real-time TTS) |
| `on_turn_complete` | Agent turn finishes |
| `on_audio_chunk` | TTS produces audio |

All callbacks are wired in `AgentSession.__init__` and fire WS events or record metrics.
