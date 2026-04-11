"""System prompt generators for the teaching and intro phases."""

from __future__ import annotations


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


def make_teaching_prompt(
    title: str,
    sections: list[dict],
    idx: int,
    lesson_goal: str | None = None,
    current_tasks: list[dict] | None = None,
) -> str:
    """System prompt for one teaching turn."""
    total = len(sections)
    sec = sections[idx]
    covered = [s["title"] for s in sections[:idx]]
    covered_str = ", ".join(covered) if covered else "none yet"

    page_range = ""
    if sec.get("page_start") and sec.get("page_end"):
        page_range = f" (pages {sec['page_start']}–{sec['page_end']})"
    elif sec.get("page_start"):
        page_range = f" (page {sec['page_start']})"

    if current_tasks:
        checklist_lines = []
        for i, task in enumerate(current_tasks):
            status_tag = "DONE" if task["status"] in ("passed", "skipped") else "PENDING"
            checklist_lines.append(f"  [{status_tag}] {i}. {task['concept']}")
        concepts_block = (
            "CONCEPT CHECKLIST (call mark_task_complete for each as the student demonstrates understanding):\n"
            + "\n".join(checklist_lines)
        )
    else:
        concepts_block = (
            "KEY CONCEPTS TO VERIFY:\n"
            + "\n".join(f"- {c}" for c in sec["key_concepts"])
        )

    goal_block = ""
    if lesson_goal:
        goal_block = (
            f"\n<goal>\n{lesson_goal}\n"
            "Tailor your teaching toward this goal.\n</goal>\n"
        )

    return (
        f'You are an expert, encouraging teacher working through "{title}" with a '
        f"student in a spoken voice conversation.\n"
        f"{goal_block}\n"
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
        "</constraints>\n\n"
        "<grounding>\n"
        "Teach from the section content below. Do not invent facts beyond it. "
        "If the student asks about topics outside this section, use search_content "
        "to find relevant material before answering.\n"
        "</grounding>\n\n"
        "<progress>\n"
        f"Section {idx + 1} of {total}. Already covered: {covered_str}.\n"
        "</progress>\n\n"
        f'<section title="{sec["title"]}"{page_range}>\n'
        f"{sec['content']}\n"
        "</section>\n\n"
        "<checklist>\n"
        f"{concepts_block}\n"
        "</checklist>\n\n"
        "<approach>\n"
        "The checklist above defines your teaching loop for this section.\n\n"
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
        "</approach>\n\n"
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
