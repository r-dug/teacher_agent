"""System prompt generators for the teaching and intro phases.

The teaching system prompt supports two modes:

1. **Default template** — used when no persona template is set.  The
   full prompt is built from ``DEFAULT_TEACHING_TEMPLATE`` with
   ``{{variable}}`` placeholders substituted at runtime.

2. **Custom persona template** — admins write a full system prompt
   template in the persona's ``instructions`` field.  The same
   ``{{variables}}`` are available.  This gives full control over
   teacher identity, tone, and operational rules.

   If a persona's ``instructions`` field contains no ``{{`` markers,
   it's treated as a legacy identity string and wrapped in the
   default template at the ``{{identity}}`` position (backward compat).

Available template variables (all substituted by ``render_template``):

    {{identity}}        — default teacher identity line (only in default template)
    {{goal}}            — student's lesson goal block (or empty)
    {{constraints}}     — voice format rules, conciseness, no-answer-leaking
    {{grounding}}       — teach-from-section, search_content for out-of-scope
    {{progress}}        — "Section N of M. Already covered: ..."
    {{section}}         — full section content with title + page range
    {{section_title}}   — just the section title string
    {{section_content}} — just the section body text
    {{page_range}}      — "(pages N–M)" or empty
    {{concepts}}        — "KEY CONCEPTS TO VERIFY" list
    {{approach}}        — TEACH/MARK/QUIZ loop instructions
    {{tools}}           — tool-principles block
    {{title}}           — lesson title
    {{covered}}         — comma-separated list of covered section titles

The volatile per-turn task checklist is NOT a template variable — it's
injected as a ``<system-reminder>`` tag on the last user message by
``make_task_status_reminder()`` (Cache Plan C1).
"""

from __future__ import annotations

import re


def make_intro_prompt(title: str, sections: list[dict], raw_text: str | None = None) -> str:
    """System prompt for the agentic goal-gathering intro loop."""
    if sections:
        titles = [s.get("title", "") for s in sections if s.get("title")]
        doc_context = f"LESSON OUTLINE: {'; '.join(titles)}"
    elif raw_text:
        preview = raw_text[:2000].strip()
        doc_context = f"DOCUMENT PREVIEW (first pages — full analysis pending):\n{preview}"
    else:
        doc_context = f'DOCUMENT: "{title}" (content will be fully analysed shortly)'

    return (
        f'You are a warm, friendly teacher about to begin a lesson on "{title}" with a student '
        f"in a spoken voice conversation.\n\n"
        f"{doc_context}\n\n"
        "YOUR ROLE: Before teaching begins, understand the student's learning goals so the "
        "lesson can be tailored specifically for them.\n\n"
        "PROCESS (at most 3 exchanges total):\n"
        "1. FIRST exchange: give a brief 1-2 sentence overview of the lesson, then ask what "
        "the student hopes to learn or achieve.\n"
        "2. If their response is clear and specific: call capture_lesson_goal immediately.\n"
        "3. If vague or you need to know their experience level: ask exactly ONE follow-up.\n"
        "4. After the follow-up, call capture_lesson_goal regardless — do not keep asking.\n\n"
        "Do NOT begin teaching yet. Ask at most 2 questions total. Be concise and welcoming.\n\n"
        "VOICE RULES: Plain prose only. No markdown, bullets, or numbered lists. "
        "Spell out numbers and abbreviations. Avoid em-dashes."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Template blocks — reusable fragments that make up the default template
# and are available as {{variables}} in custom templates.
# ─────────────────────────────────────────────────────────────────────────────

CONSTRAINTS_BLOCK = (
    "<constraints>\n"
    "VOICE FORMAT: Plain prose only. No markdown, bullets, or numbered lists. "
    "Spell out numbers and abbreviations. Avoid em-dashes.\n\n"
    "ALWAYS INCLUDE SPEECH: Every response must contain spoken text alongside "
    "any tool call. The student cannot see tool calls — a tool-only response "
    "sounds like silence.\n\n"
    "CONCISENESS: Two to four sentences maximum before pausing for a question, "
    "exercise, or tool call. Prefer activities over explanation. "
    "Never repeat or restate what you already said.\n\n"
    "NO ANSWER LEAKING: When testing recall (quizzes, sketchpad, fill-in-the-blank), "
    "do not reveal the answer in your spoken text or in the tool's prompt field. "
    "The student must produce it from memory. "
    'Wrong: "Write し for me." '
    'Right: "Write the hiragana for the shi sound."\n'
    "</constraints>"
)

GROUNDING_BLOCK = (
    "<grounding>\n"
    "Teach from the section content below. Do not invent facts beyond it. "
    "If the student asks about topics outside this section, use search_content "
    "to find relevant material before answering.\n"
    "</grounding>"
)

APPROACH_BLOCK = (
    "<approach>\n"
    "A <system-reminder> block appended to each user message shows the live "
    "DONE/PENDING status of every concept in this section.  Use it to drive "
    "the teaching loop:\n\n"
    "1. TEACH — Work through the first PENDING concept. Explain it briefly, "
    "then verify with a question or exercise. Do not skip ahead.\n"
    "2. MARK — When the student demonstrates understanding, you MUST call "
    "mark_task_complete for that concept in the SAME response. Every response "
    "where the student shows understanding should include a mark_task_complete "
    "call. If you do not mark it, the lesson cannot progress.\n"
    "3. QUIZ — Once every non-quiz task is DONE, quiz the student across the "
    "full section. If they reveal weakness, call unmark_task and return to "
    "step 1. If they pass, mark the quiz task complete.\n\n"
    "The system advances to the next section automatically once all tasks "
    "are DONE. You MUST call mark_task_complete to make progress.\n"
    "</approach>"
)

TOOLS_BLOCK = (
    "<tool-principles>\n"
    "Use tools to create exercises instead of explaining. Key patterns:\n"
    "- Writing or drawing: open_sketchpad (omit text_bg to test recall).\n"
    "- Comprehension checks: show_quiz, fill_in_the_blank, ordering_exercise.\n"
    "- Free-response or formulas: text_input.\n"
    "- Vocabulary review: show_flashcard_deck.\n"
    "- Timed drills: start_timer.\n"
    "- Pronunciation or listening: play_audio_clip.\n"
    "- Out-of-scope questions: search_content.\n"
    "</tool-principles>"
)

# The default template — used when the persona has no custom template.
DEFAULT_TEACHING_TEMPLATE = """{{identity}}
{{goal}}
{{constraints}}

{{grounding}}

{{progress}}

{{section}}

{{concepts}}

{{approach}}

{{tools}}"""


# ─────────────────────────────────────────────────────────────────────────────
# Template rendering
# ─────────────────────────────────────────────────────────────────────────────


def _build_template_vars(
    title: str,
    sections: list[dict],
    idx: int,
    lesson_goal: str | None = None,
) -> dict[str, str]:
    """Build the variable dict for template substitution."""
    total = len(sections)
    sec = sections[idx]
    covered = [s["title"] for s in sections[:idx]]
    covered_str = ", ".join(covered) if covered else "none yet"

    page_range = ""
    if sec.get("page_start") and sec.get("page_end"):
        page_range = f" (pages {sec['page_start']}–{sec['page_end']})"
    elif sec.get("page_start"):
        page_range = f" (page {sec['page_start']})"

    goal_block = ""
    if lesson_goal:
        goal_block = (
            f"<goal>\n{lesson_goal}\n"
            "Tailor your teaching toward this goal.\n</goal>"
        )

    key_concepts = sec.get("key_concepts") or []
    concepts_block = (
        "<checklist>\n"
        "KEY CONCEPTS TO VERIFY:\n"
        + "\n".join(f"- {c}" for c in key_concepts)
        + "\n</checklist>"
    )

    section_block = (
        f'<section title="{sec["title"]}"{page_range}>\n'
        f"{sec['content']}\n"
        "</section>"
    )

    progress_block = (
        "<progress>\n"
        f"Section {idx + 1} of {total}. Already covered: {covered_str}.\n"
        "</progress>"
    )

    identity = (
        f'You are an expert, encouraging teacher working through "{title}" with a '
        f"student in a spoken voice conversation."
    )

    return {
        "identity": identity,
        "goal": goal_block,
        "constraints": CONSTRAINTS_BLOCK,
        "grounding": GROUNDING_BLOCK,
        "progress": progress_block,
        "section": section_block,
        "section_title": sec["title"],
        "section_content": sec.get("content", ""),
        "page_range": page_range,
        "concepts": concepts_block,
        "approach": APPROACH_BLOCK,
        "tools": TOOLS_BLOCK,
        "title": title,
        "covered": covered_str,
    }


def render_template(template: str, variables: dict[str, str]) -> str:
    """Substitute ``{{variable}}`` placeholders in a template string.

    Unknown variables are left as-is (not stripped) so admins can spot
    typos.  Double-brace escaping (``{{{{``) is not supported — this
    is a simple teaching-prompt renderer, not Jinja.
    """
    def _replace(match):
        key = match.group(1).strip()
        return variables.get(key, match.group(0))

    result = re.sub(r"\{\{(\w+)\}\}", _replace, template)
    # Collapse runs of 3+ blank lines into 2 (cleanup after empty blocks).
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _is_template(text: str) -> bool:
    """Return True if ``text`` contains at least one ``{{variable}}`` marker."""
    return "{{" in text and "}}" in text


def make_teaching_system_prompt(
    title: str,
    sections: list[dict],
    idx: int,
    lesson_goal: str | None = None,
    persona_template: str | None = None,
) -> str:
    """Build the stable teaching system prompt.

    If ``persona_template`` is provided and contains ``{{`` markers,
    it's rendered as a full custom template with variable substitution.

    If ``persona_template`` is provided but contains NO ``{{`` markers,
    it's treated as a legacy identity/style string and injected at the
    ``{{identity}}`` position of the default template (backward compat
    with existing personas whose ``instructions`` field is just a
    personality description).

    If ``persona_template`` is None or empty, the default template is
    used with the generic "expert, encouraging teacher" identity.

    Deliberately does NOT take a ``current_tasks`` argument — the
    per-turn DONE/PENDING status is produced by
    ``make_task_status_reminder`` and appended to the last user message
    before each LLM call.
    """
    variables = _build_template_vars(title, sections, idx, lesson_goal)

    if persona_template and persona_template.strip():
        if _is_template(persona_template):
            # Full custom template — render it directly.
            return render_template(persona_template, variables)
        else:
            # Legacy persona instructions — use as identity, append
            # VOICE RULES if not already present, then render default
            # template with the custom identity.
            variables["identity"] = persona_template.strip()
            return render_template(DEFAULT_TEACHING_TEMPLATE, variables)

    return render_template(DEFAULT_TEACHING_TEMPLATE, variables)


def make_task_status_reminder(current_tasks: list[dict] | None) -> str:
    """Volatile per-turn task status block.

    Returns an empty string when ``current_tasks`` is None or empty
    (intro phase or edge cases).  Otherwise returns a ``<system-reminder>``
    tag listing each concept with its ``[DONE]`` or ``[PENDING]`` flag.
    The teacher agent appends this block to the trailing user message
    of ``llm_messages`` before each call so that the LLM sees the
    live task state without invalidating the cached system prompt.
    """
    if not current_tasks:
        return ""
    lines: list[str] = []
    for i, task in enumerate(current_tasks):
        status_tag = "DONE" if task["status"] in ("passed", "skipped") else "PENDING"
        lines.append(f"  [{status_tag}] {i}. {task['concept']}")
    return (
        "<system-reminder>\n"
        "CONCEPT CHECKLIST (call mark_task_complete for each as the student demonstrates understanding):\n"
        + "\n".join(lines)
        + "\n</system-reminder>"
    )


def make_teaching_prompt(
    title: str,
    sections: list[dict],
    idx: int,
    lesson_goal: str | None = None,
    current_tasks: list[dict] | None = None,
    persona_template: str | None = None,
) -> str:
    """Back-compat helper: flat string combining system prompt + task reminder.

    Used by call sites that can't consume the split shape (the Realtime
    API path in ``ws_session.py`` and the eval seed generator).  New
    call sites should prefer ``make_teaching_system_prompt()`` plus
    ``make_task_status_reminder()`` so caching stays effective.
    """
    system = make_teaching_system_prompt(
        title, sections, idx, lesson_goal, persona_template=persona_template,
    )
    reminder = make_task_status_reminder(current_tasks)
    if reminder:
        return system + "\n\n" + reminder
    return system
