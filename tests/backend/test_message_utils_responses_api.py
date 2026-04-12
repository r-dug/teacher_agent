"""Tests for the OpenAI Responses API message translator.

Plan B follow-up A2: verifies that ``_messages_to_openai_responses``
produces the correct Responses API ``input`` items from our internal
Anthropic-format message history.
"""

from __future__ import annotations

import json

from backend.services.agents.message_utils import (
    _messages_to_openai_responses,
    _tool_schema_to_openai_responses,
)


def test_simple_user_message():
    """A plain-text user message becomes a message item with input_text."""
    items = _messages_to_openai_responses([
        {"role": "user", "content": "Hello, world"},
    ])
    assert len(items) == 1
    assert items[0]["type"] == "message"
    assert items[0]["role"] == "user"
    assert items[0]["content"] == [{"type": "input_text", "text": "Hello, world"}]


def test_assistant_text_message_uses_output_text():
    """An assistant text message uses output_text, not input_text."""
    items = _messages_to_openai_responses([
        {"role": "assistant", "content": [{"type": "text", "text": "Sure, here you go."}]},
    ])
    assert len(items) == 1
    assert items[0]["type"] == "message"
    assert items[0]["role"] == "assistant"
    assert items[0]["content"] == [{"type": "output_text", "text": "Sure, here you go."}]


def test_assistant_string_content_uses_output_text():
    """Regression: assistant messages with plain STRING content (not a list)
    must use ``output_text``, not ``input_text``.  The Responses API rejects
    ``input_text`` on assistant messages with error code 400.

    This bug was introduced when the string-content shortcut didn't check
    the message role before choosing the content block type."""
    items = _messages_to_openai_responses([
        {"role": "assistant", "content": "Sure thing."},
    ])
    assert len(items) == 1
    assert items[0]["type"] == "message"
    assert items[0]["role"] == "assistant"
    assert items[0]["content"] == [{"type": "output_text", "text": "Sure thing."}]


def test_tool_call_and_result_become_inline_items():
    """A full turn (user question → assistant tool_use → user tool_result)
    produces input items in the right shape: message, function_call,
    function_call_output."""
    messages = [
        {"role": "user", "content": "draw something"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "OK, try this."},
                {
                    "type": "tool_use",
                    "id": "call_abc",
                    "name": "open_sketchpad",
                    "input": {"prompt": "Draw a triangle"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_abc",
                    "content": "OK",
                },
            ],
        },
    ]
    items = _messages_to_openai_responses(messages)

    # Expect 4 items: user message, assistant message, function_call, function_call_output
    kinds = [i["type"] for i in items]
    assert kinds == ["message", "message", "function_call", "function_call_output"]

    # Assistant text message
    assert items[1]["role"] == "assistant"
    assert items[1]["content"][0]["type"] == "output_text"

    # Function call item
    assert items[2]["call_id"] == "call_abc"
    assert items[2]["name"] == "open_sketchpad"
    assert json.loads(items[2]["arguments"]) == {"prompt": "Draw a triangle"}

    # Function call output item
    assert items[3]["call_id"] == "call_abc"
    assert items[3]["output"] == "OK"


def test_orphaned_tool_use_is_dropped():
    """Assistant tool_use without a matching tool_result must be stripped —
    Responses API rejects function_call items with no matching output."""
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me check..."},
                {
                    "type": "tool_use",
                    "id": "call_orphan",
                    "name": "show_quiz",
                    "input": {"prompt": "Q?"},
                },
            ],
        },
        # No tool_result with tool_use_id=call_orphan follows
    ]
    items = _messages_to_openai_responses(messages)
    # Only the text message survives; the function_call is dropped.
    assert len(items) == 1
    assert items[0]["type"] == "message"
    assert items[0]["content"][0]["type"] == "output_text"


def test_tool_result_with_image_becomes_followup_user_message():
    """A tool_result with an image payload produces a function_call_output
    for the text portion AND a follow-up user message with input_image."""
    messages = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_sketch",
                    "name": "open_sketchpad",
                    "input": {"prompt": "draw"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_sketch",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": "BASE64PNG",
                            },
                        },
                        {"type": "text", "text": "Student drew this."},
                    ],
                },
            ],
        },
    ]
    items = _messages_to_openai_responses(messages)

    # Expect: function_call (from the assistant), function_call_output
    # (for the tool result text), user message with input_image (for the
    # image portion).
    kinds = [i["type"] for i in items]
    assert "function_call" in kinds
    assert "function_call_output" in kinds
    assert "message" in kinds

    msg_items = [i for i in items if i["type"] == "message"]
    assert len(msg_items) == 1
    content = msg_items[0]["content"]
    assert any(c.get("type") == "input_image" for c in content)
    image_block = next(c for c in content if c.get("type") == "input_image")
    assert image_block["image_url"].startswith("data:image/png;base64,BASE64PNG")


def test_plain_image_block_in_user_message():
    """User message with a raw image (not tool_result) produces a single
    message item with input_text + input_image content."""
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What's this?"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": "JPEGBASE64",
                    },
                },
            ],
        },
    ]
    items = _messages_to_openai_responses(messages)
    assert len(items) == 1
    assert items[0]["type"] == "message"
    content = items[0]["content"]
    assert len(content) == 2
    assert content[0]["type"] == "input_text"
    assert content[1]["type"] == "input_image"
    assert content[1]["image_url"].startswith("data:image/jpeg;base64,JPEGBASE64")


def test_tool_schema_flat_shape():
    """_tool_schema_to_openai_responses produces flat shape (no nested 'function' key)."""
    schema = _tool_schema_to_openai_responses({
        "name": "my_tool",
        "description": "does stuff",
        "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}},
    })
    assert schema == {
        "type": "function",
        "name": "my_tool",
        "description": "does stuff",
        "parameters": {"type": "object", "properties": {"x": {"type": "string"}}},
    }


def test_thinking_blocks_skipped():
    """Anthropic 'thinking' blocks are filtered out — OpenAI doesn't understand them."""
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "internal reasoning..."},
                {"type": "text", "text": "Final answer."},
            ],
        },
    ]
    items = _messages_to_openai_responses(messages)
    assert len(items) == 1
    assert items[0]["content"][0]["text"] == "Final answer."
