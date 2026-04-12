"""Pure format-conversion helpers for LLM message history.

All functions are stateless and have no side effects.
"""

from __future__ import annotations

import json


def _block_to_api_dict(block) -> dict:
    """Convert an Anthropic SDK content block to a plain dict the API accepts.

    model_dump() includes SDK-internal fields (e.g. parsed_output) that cause
    a 400 invalid_request_error on the next API call.  We keep only the fields
    that are part of the public API schema.
    """
    t = getattr(block, "type", None)
    if t == "text":
        return {"type": "text", "text": block.text}
    if t == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if t == "thinking":
        d: dict = {"type": "thinking", "thinking": block.thinking}
        # signature is required when passing thinking blocks back to the API
        if getattr(block, "signature", None):
            d["signature"] = block.signature
        return d
    # Fallback for unknown block types
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return dict(block)


def _tool_schema_to_openai(tool: dict) -> dict:
    """Convert Anthropic-style tool schema to OpenAI Chat Completions format.

    Plan B follow-up A2: Chat Completions is being phased out in favor of
    Responses API; see ``_tool_schema_to_openai_responses`` below.  This
    function is kept temporarily for any test fakes that still construct
    the old shape and will be deleted once nothing references it.
    """
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
        },
    }


def _tool_schema_to_openai_responses(tool: dict) -> dict:
    """Convert Anthropic-style tool schema to OpenAI Responses API format.

    Responses API uses a flatter shape than Chat Completions: name,
    description, parameters, and type are all top-level keys (no nested
    ``function`` object).
    """
    return {
        "type": "function",
        "name": tool.get("name", ""),
        "description": tool.get("description", ""),
        "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
    }


def _tool_result_content_to_text_and_images(content) -> tuple[str, list[dict]]:
    """
    Convert Anthropic tool_result content to a tool text payload plus optional
    user multimodal blocks (for image-bearing submissions).
    """
    if isinstance(content, str):
        text = content.strip()
        return (text if text else "OK"), []

    if not isinstance(content, list):
        return "OK", []

    text_parts: list[str] = []
    user_blocks: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            txt = (block.get("text") or "").strip()
            if txt:
                text_parts.append(txt)
        elif btype == "image":
            src = block.get("source") or {}
            if src.get("type") == "base64":
                media_type = src.get("media_type", "image/png")
                data = src.get("data", "")
                if data:
                    user_blocks.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{data}"},
                    })

    tool_text = "\n".join(text_parts).strip()
    if not tool_text and user_blocks:
        tool_text = f"Student submitted {len(user_blocks)} image(s)."
    if not tool_text:
        tool_text = "OK"

    if user_blocks:
        user_blocks.insert(0, {"type": "text", "text": "Tool result images from the student."})
    return tool_text, user_blocks


def _messages_to_openai(messages: list[dict]) -> list[dict]:
    """
    Convert internal Anthropic-style message history to OpenAI chat messages.

    This preserves tool-call chains and also forwards image-bearing tool results
    as follow-up user multimodal messages.

    OpenAI requires every tool_call_id in an assistant message to have a matching
    tool-role response.  Our agent loop only processes one tool per LLM turn, so
    extra tool_use blocks may lack responses.  We collect all answered IDs first
    and strip unanswered tool_calls to avoid 400 errors.
    """
    # Pre-scan: collect all tool_use_ids that have a matching tool_result
    _answered_ids: set[str] = set()
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tid = block.get("tool_use_id")
                if tid:
                    _answered_ids.add(tid)

    out: list[dict] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if not isinstance(content, list):
            out.append({"role": role, "content": str(content)})
            continue

        if role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                # Skip thinking/redacted_thinking blocks — OpenAI doesn't understand them
                if btype in ("thinking", "redacted_thinking"):
                    continue
                if btype == "text":
                    txt = (block.get("text") or "").strip()
                    if txt:
                        text_parts.append(txt)
                elif btype == "tool_use":
                    tc_id = block.get("id") or ""
                    # Only include tool_calls that have a matching tool_result;
                    # orphaned calls cause OpenAI 400 errors.
                    if tc_id and tc_id not in _answered_ids:
                        continue
                    try:
                        args_json = json.dumps(block.get("input") or {})
                    except Exception:
                        args_json = "{}"
                    tool_calls.append({
                        "id": tc_id,
                        "type": "function",
                        "function": {
                            "name": block.get("name") or "",
                            "arguments": args_json,
                        },
                    })

            if text_parts or tool_calls:
                msg_out: dict = {
                    "role": "assistant",
                    "content": "\n".join(text_parts).strip() if text_parts else None,
                }
                if tool_calls:
                    msg_out["tool_calls"] = tool_calls
                out.append(msg_out)
            continue

        if role == "user":
            plain_text_parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    txt = (block.get("text") or "").strip()
                    if txt:
                        plain_text_parts.append(txt)
                elif btype == "tool_result":
                    tool_call_id = block.get("tool_use_id") or ""
                    tool_text, user_blocks = _tool_result_content_to_text_and_images(
                        block.get("content")
                    )
                    out.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": tool_text,
                    })
                    if user_blocks:
                        out.append({"role": "user", "content": user_blocks})

            if plain_text_parts:
                out.append({"role": "user", "content": "\n".join(plain_text_parts).strip()})
            continue

        # Fallback for unexpected roles
        out.append({"role": "user", "content": json.dumps(content)})

    return out


def _tool_result_content_to_responses_text_and_images(content) -> tuple[str, list[dict]]:
    """Convert Anthropic tool_result content to a Responses API ``input_text``
    payload plus optional ``input_image`` blocks (for image-bearing
    submissions).

    Plan B follow-up A2: Responses API uses ``input_image`` (with
    ``image_url`` as a string field, not a nested object) instead of
    Chat Completions' ``image_url: {url: ...}`` shape.
    """
    if isinstance(content, str):
        text = content.strip()
        return (text if text else "OK"), []

    if not isinstance(content, list):
        return "OK", []

    text_parts: list[str] = []
    user_blocks: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            txt = (block.get("text") or "").strip()
            if txt:
                text_parts.append(txt)
        elif btype == "image":
            src = block.get("source") or {}
            if src.get("type") == "base64":
                media_type = src.get("media_type", "image/png")
                data = src.get("data", "")
                if data:
                    user_blocks.append({
                        "type": "input_image",
                        "image_url": f"data:{media_type};base64,{data}",
                    })

    tool_text = "\n".join(text_parts).strip()
    if not tool_text and user_blocks:
        tool_text = f"Student submitted {len(user_blocks)} image(s)."
    if not tool_text:
        tool_text = "OK"
    return tool_text, user_blocks


def _messages_to_openai_responses(messages: list[dict]) -> list[dict]:
    """Convert internal Anthropic-style message history to OpenAI Responses
    API ``input`` items.

    Plan B follow-up A2: replaces ``_messages_to_openai`` for the new
    Responses API surface.  The Responses API ``input`` is a flat list of
    items where each item is one of:

    - ``{type: "message", role: "user"|"assistant"|"system", content: [...]}``
      where content is a list of input_text/output_text/input_image blocks
    - ``{type: "function_call", call_id, name, arguments: <json string>}``
      — represents a tool call the model made on a prior turn
    - ``{type: "function_call_output", call_id, output: <text>}``
      — represents the result of that tool call

    Tool calls and their outputs live as **inline items** in the input
    list, not as a separate assistant-message-with-tool_calls + tool-role
    message pair.  This is the biggest structural difference from
    ``_messages_to_openai``.

    Image-bearing tool results are split: the text portion goes into a
    ``function_call_output`` item; image blocks become a follow-up
    ``message`` item with ``input_image`` content (so the model can
    actually see the image).

    Orphaned tool_use blocks (no matching tool_result) are filtered out
    to avoid 400 errors from the API.
    """
    # Pre-scan: collect all tool_use_ids that have a matching tool_result.
    _answered_ids: set[str] = set()
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tid = block.get("tool_use_id")
                if tid:
                    _answered_ids.add(tid)

    out: list[dict] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            # Plain string content.  Assistant messages must use
            # ``output_text`` in the Responses API; user messages
            # use ``input_text``.
            text_type = "output_text" if role == "assistant" else "input_text"
            out.append({
                "type": "message",
                "role": role,
                "content": [{"type": text_type, "text": content}],
            })
            continue

        if not isinstance(content, list):
            text_type = "output_text" if role == "assistant" else "input_text"
            out.append({
                "type": "message",
                "role": role,
                "content": [{"type": text_type, "text": str(content)}],
            })
            continue

        if role == "assistant":
            # Walk the assistant's content blocks and emit:
            #   1. (if text) a message item with output_text content
            #   2. (per tool_use) a function_call item
            text_parts: list[str] = []
            function_calls: list[dict] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype in ("thinking", "redacted_thinking"):
                    continue
                if btype == "text":
                    txt = (block.get("text") or "").strip()
                    if txt:
                        text_parts.append(txt)
                elif btype == "tool_use":
                    tc_id = block.get("id") or ""
                    if tc_id and tc_id not in _answered_ids:
                        # Orphaned tool call — drop to avoid API error.
                        continue
                    try:
                        args_json = json.dumps(block.get("input") or {})
                    except Exception:
                        args_json = "{}"
                    function_calls.append({
                        "type": "function_call",
                        "call_id": tc_id,
                        "name": block.get("name") or "",
                        "arguments": args_json,
                    })

            if text_parts:
                out.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "\n".join(text_parts).strip()}],
                })
            out.extend(function_calls)
            continue

        if role == "user":
            # Walk the user's content blocks and emit a mix of:
            #   - (per tool_result) a function_call_output item; if the
            #     tool result has images, follow with a message item
            #     containing input_image blocks so the model can see them
            #   - (per text block) merged into a single message item
            #   - (per image block) inline input_image inside a message
            plain_text_parts: list[str] = []
            inline_image_blocks: list[dict] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    txt = (block.get("text") or "").strip()
                    if txt:
                        plain_text_parts.append(txt)
                elif btype == "image":
                    src = block.get("source") or {}
                    if src.get("type") == "base64":
                        media_type = src.get("media_type", "image/png")
                        data = src.get("data", "")
                        if data:
                            inline_image_blocks.append({
                                "type": "input_image",
                                "image_url": f"data:{media_type};base64,{data}",
                            })
                elif btype == "tool_result":
                    tc_id = block.get("tool_use_id") or ""
                    tool_text, image_blocks = (
                        _tool_result_content_to_responses_text_and_images(
                            block.get("content")
                        )
                    )
                    out.append({
                        "type": "function_call_output",
                        "call_id": tc_id,
                        "output": tool_text,
                    })
                    if image_blocks:
                        # Follow with a user message containing the images
                        # so the model can actually see them.
                        out.append({
                            "type": "message",
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": "Tool result images from the student."},
                                *image_blocks,
                            ],
                        })

            # If the user message had text and/or inline images, emit a
            # single message item containing them.
            if plain_text_parts or inline_image_blocks:
                content_items: list[dict] = []
                if plain_text_parts:
                    content_items.append({
                        "type": "input_text",
                        "text": "\n".join(plain_text_parts).strip(),
                    })
                content_items.extend(inline_image_blocks)
                out.append({
                    "type": "message",
                    "role": "user",
                    "content": content_items,
                })
            continue

        # Fallback for unexpected roles — wrap content as JSON text.
        out.append({
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": json.dumps(content)}],
        })

    return out
