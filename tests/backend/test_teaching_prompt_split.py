"""Tests for the cache-friendly teaching prompt split.

Cache Plan Commit 1: ``make_teaching_system_prompt`` must be stable
across turns within a section (no per-turn state leaks in) so that
prefix caching on both OpenAI and Anthropic actually works.
``make_task_status_reminder`` carries the volatile DONE/PENDING
per-task state; the teacher agent appends it to the trailing user
message of ``llm_messages`` right before each LLM call.

The back-compat ``make_teaching_prompt`` concatenates the two and is
kept for the Realtime API path and the eval seed generator.
"""

from __future__ import annotations

import pytest

from backend.services.agents.prompts.teaching import (
    APPROACH_BLOCK,
    CONSTRAINTS_BLOCK,
    DEFAULT_TEACHING_TEMPLATE,
    TOOLS_BLOCK,
    make_task_status_reminder,
    make_teaching_prompt,
    make_teaching_system_prompt,
    render_template,
)


def _make_sections():
    return [
        {
            "title": "Section One",
            "content": "Section one content about topic A.",
            "key_concepts": ["Concept A", "Concept B"],
            "page_start": 1,
            "page_end": 2,
        },
        {
            "title": "Section Two",
            "content": "Section two content about topic B.",
            "key_concepts": ["Concept C"],
            "page_start": 3,
            "page_end": 4,
        },
    ]


# ── make_teaching_system_prompt ──────────────────────────────────────────────


class TestTeachingSystemPrompt:
    def test_contains_stable_section_content(self):
        sections = _make_sections()
        system = make_teaching_system_prompt("Lesson", sections, 0)
        assert "Section one content about topic A." in system
        assert "Section One" in system
        assert "Section 1 of 2" in system
        assert "KEY CONCEPTS TO VERIFY" in system
        assert "- Concept A" in system
        assert "- Concept B" in system

    def test_does_not_contain_task_status(self):
        """The stable prompt must NOT contain DONE/PENDING — that's volatile."""
        sections = _make_sections()
        system = make_teaching_system_prompt("Lesson", sections, 0)
        assert "[DONE]" not in system
        assert "[PENDING]" not in system

    def test_stable_across_task_progress_changes(self):
        """Byte-identical system prompt regardless of task state.

        The whole point of the split: the cached prefix must survive
        every mark_task_complete call within a section.
        """
        sections = _make_sections()
        system_a = make_teaching_system_prompt("Lesson", sections, 0, lesson_goal="learn it")
        system_b = make_teaching_system_prompt("Lesson", sections, 0, lesson_goal="learn it")
        assert system_a == system_b

    def test_changes_when_section_advances(self):
        """Section advancement IS a cache invalidation point — that's expected."""
        sections = _make_sections()
        system_s1 = make_teaching_system_prompt("Lesson", sections, 0)
        system_s2 = make_teaching_system_prompt("Lesson", sections, 1)
        assert system_s1 != system_s2
        assert "Section One" in system_s1
        assert "Section Two" in system_s2

    def test_lesson_goal_included(self):
        sections = _make_sections()
        system = make_teaching_system_prompt(
            "Lesson", sections, 0, lesson_goal="master it"
        )
        assert "<goal>" in system
        assert "master it" in system

    def test_lesson_goal_absent_when_none(self):
        sections = _make_sections()
        system = make_teaching_system_prompt("Lesson", sections, 0)
        assert "<goal>" not in system

    def test_no_current_tasks_parameter(self):
        """The stable prompt must not accept per-turn task state at all."""
        import inspect

        sig = inspect.signature(make_teaching_system_prompt)
        assert "current_tasks" not in sig.parameters


# ── make_task_status_reminder ────────────────────────────────────────────────


class TestTaskStatusReminder:
    def test_empty_when_no_tasks(self):
        assert make_task_status_reminder(None) == ""
        assert make_task_status_reminder([]) == ""

    def test_wraps_in_system_reminder_tag(self):
        tasks = [
            {"concept": "Concept A", "status": "passed", "evidence": None},
            {"concept": "Concept B", "status": "pending", "evidence": None},
        ]
        reminder = make_task_status_reminder(tasks)
        assert reminder.startswith("<system-reminder>")
        assert reminder.endswith("</system-reminder>")

    def test_renders_done_and_pending(self):
        tasks = [
            {"concept": "Concept A", "status": "passed", "evidence": None},
            {"concept": "Concept B", "status": "pending", "evidence": None},
            {"concept": "Concept C", "status": "skipped", "evidence": None},
        ]
        reminder = make_task_status_reminder(tasks)
        assert "[DONE] 0. Concept A" in reminder
        assert "[PENDING] 1. Concept B" in reminder
        assert "[DONE] 2. Concept C" in reminder  # skipped renders as DONE

    def test_mentions_mark_task_complete(self):
        tasks = [{"concept": "Concept A", "status": "pending", "evidence": None}]
        reminder = make_task_status_reminder(tasks)
        assert "mark_task_complete" in reminder


# ── back-compat make_teaching_prompt ─────────────────────────────────────────


class TestBackCompatFlatPrompt:
    def test_contains_both_pieces(self):
        """``make_teaching_prompt`` must still emit the old flat shape."""
        sections = _make_sections()
        tasks = [
            {"concept": "Concept A", "status": "passed", "evidence": None},
            {"concept": "Concept B", "status": "pending", "evidence": None},
        ]
        flat = make_teaching_prompt(
            "Lesson", sections, 0, lesson_goal="go", current_tasks=tasks
        )
        assert "<goal>" in flat
        assert "go" in flat
        assert "Section one content about topic A." in flat
        # Task status survives via the reminder block.
        assert "[DONE] 0. Concept A" in flat
        assert "[PENDING] 1. Concept B" in flat
        assert "CONCEPT CHECKLIST" in flat

    def test_flat_matches_system_plus_reminder(self):
        sections = _make_sections()
        tasks = [{"concept": "Concept A", "status": "pending", "evidence": None}]
        system = make_teaching_system_prompt("Lesson", sections, 0, lesson_goal="go")
        reminder = make_task_status_reminder(tasks)
        flat = make_teaching_prompt(
            "Lesson", sections, 0, lesson_goal="go", current_tasks=tasks
        )
        assert flat == system + "\n\n" + reminder

    def test_flat_without_tasks_is_just_system(self):
        sections = _make_sections()
        flat = make_teaching_prompt("Lesson", sections, 0)
        system = make_teaching_system_prompt("Lesson", sections, 0)
        assert flat == system


# ── persona template system ──────────────────────────────────────────────────


class TestPersonaTemplate:
    def test_no_template_uses_default_identity(self):
        """No persona → default 'expert, encouraging teacher' identity."""
        sections = _make_sections()
        system = make_teaching_system_prompt("Lesson", sections, 0)
        assert "expert, encouraging teacher" in system

    def test_legacy_persona_replaces_identity(self):
        """Legacy persona (no {{variables}}) replaces the identity line."""
        sections = _make_sections()
        persona = "You are Tsumugi, a brilliantly snide tutor."
        system = make_teaching_system_prompt(
            "Lesson", sections, 0, persona_template=persona,
        )
        assert "Tsumugi" in system
        assert "brilliantly snide" in system
        # Default identity should be gone.
        assert "expert, encouraging teacher" not in system
        # Operational blocks should still be present.
        assert "<constraints>" in system
        assert "<approach>" in system
        assert "<tool-principles>" in system
        assert "Section one content about topic A." in system

    def test_full_template_with_variables(self):
        """Full custom template with {{variables}} gets substituted."""
        sections = _make_sections()
        custom = (
            "I am a custom teacher for {{title}}.\n"
            "{{section}}\n"
            "{{approach}}"
        )
        system = make_teaching_system_prompt(
            "Lesson", sections, 0, persona_template=custom,
        )
        assert "I am a custom teacher for Lesson." in system
        assert "Section one content about topic A." in system
        assert "mark_task_complete" in system  # from approach block
        # Default identity should NOT appear (custom template replaces everything).
        assert "expert, encouraging teacher" not in system

    def test_unknown_variables_left_as_is(self):
        """Unknown {{placeholders}} survive so admins can spot typos."""
        sections = _make_sections()
        custom = "Hello {{unknown_var}}. {{section}}"
        system = make_teaching_system_prompt(
            "Lesson", sections, 0, persona_template=custom,
        )
        assert "{{unknown_var}}" in system
        assert "Section one content" in system

    def test_template_has_all_expected_variables(self):
        """All documented variables resolve to non-empty strings."""
        sections = _make_sections()
        template = (
            "{{identity}} {{goal}} {{constraints}} {{grounding}} "
            "{{progress}} {{section}} {{section_title}} "
            "{{section_content}} {{concepts}} {{approach}} "
            "{{tools}} {{title}} {{covered}}"
        )
        system = make_teaching_system_prompt(
            "Lesson", sections, 0,
            lesson_goal="learn stuff",
            persona_template=template,
        )
        assert "{{" not in system  # all variables resolved

    def test_stable_across_turns_with_template(self):
        """A custom template is still stable across turns (no task state)."""
        sections = _make_sections()
        custom = "Custom teacher. {{section}} {{approach}}"
        s1 = make_teaching_system_prompt(
            "Lesson", sections, 0, persona_template=custom,
        )
        s2 = make_teaching_system_prompt(
            "Lesson", sections, 0, persona_template=custom,
        )
        assert s1 == s2

    def test_page_range_variable(self):
        sections = _make_sections()
        custom = "Pages: {{page_range}}"
        system = make_teaching_system_prompt(
            "Lesson", sections, 0, persona_template=custom,
        )
        assert "(pages 1–2)" in system

    def test_covered_variable(self):
        sections = _make_sections()
        custom = "Covered: {{covered}}"
        system = make_teaching_system_prompt(
            "Lesson", sections, 1, persona_template=custom,
        )
        assert "Section One" in system

    def test_default_template_renders_cleanly(self):
        """The DEFAULT_TEACHING_TEMPLATE renders with no leftover markers."""
        sections = _make_sections()
        system = render_template(
            DEFAULT_TEACHING_TEMPLATE,
            {"identity": "I am a teacher.", "goal": "", "constraints": CONSTRAINTS_BLOCK,
             "grounding": "grounding", "progress": "progress",
             "section": "section", "concepts": "concepts",
             "approach": APPROACH_BLOCK, "tools": TOOLS_BLOCK,
             "title": "T", "covered": "none"},
        )
        assert "{{" not in system
