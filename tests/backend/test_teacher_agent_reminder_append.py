"""Tests for the cache-plan reminder-append helper.

Cache Plan Commit 1: ``_append_reminder_to_last_user_message`` mutates
``llm_messages`` by appending a ``<system-reminder>`` block to the
trailing user message's content.  Cacheable prefix (system + prior
history) is unaffected; only the last message (which is new every
turn anyway) gets the reminder trailer.
"""

from __future__ import annotations

from backend.services.agents.teacher_agent import (
    _append_reminder_to_last_user_message,
)


def test_appends_to_string_content_user_message():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "teach me X"},
    ]
    _append_reminder_to_last_user_message(msgs, "<system-reminder>R</system-reminder>")
    assert msgs[-1]["content"] == "teach me X\n\n<system-reminder>R</system-reminder>"
    # Prior messages must be untouched so the cached prefix survives.
    assert msgs[0]["content"] == "hi"
    assert msgs[1]["content"] == "hello"


def test_appends_to_list_content_user_message():
    """Tool-result user messages use list content; the reminder goes as a new text block."""
    msgs = [
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": "result",
                },
            ],
        },
    ]
    _append_reminder_to_last_user_message(msgs, "<system-reminder>R</system-reminder>")
    last_content = msgs[-1]["content"]
    assert isinstance(last_content, list)
    assert len(last_content) == 2
    assert last_content[0]["type"] == "tool_result"
    assert last_content[1] == {"type": "text", "text": "<system-reminder>R</system-reminder>"}


def test_skips_trailing_assistant_message():
    """Walks backward to find the most recent user message."""
    msgs = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
    ]
    _append_reminder_to_last_user_message(msgs, "R")
    # Falls back to the only user message.
    assert msgs[0]["content"] == "u1\n\nR"
    assert msgs[1]["content"] == "a1"


def test_no_user_message_is_safe():
    msgs = [{"role": "assistant", "content": "a1"}]
    _append_reminder_to_last_user_message(msgs, "R")
    # No user message — reminder is silently dropped.
    assert msgs == [{"role": "assistant", "content": "a1"}]


def test_does_not_mutate_original_message_dict():
    """The helper writes back a shallow copy so shared dicts don't leak."""
    original = {"role": "user", "content": "hi"}
    msgs = [original]
    _append_reminder_to_last_user_message(msgs, "R")
    # The list slot was replaced with a new dict; the original dict the
    # caller might still hold a reference to is untouched.
    assert original == {"role": "user", "content": "hi"}
    assert msgs[0] is not original
    assert msgs[0]["content"] == "hi\n\nR"
