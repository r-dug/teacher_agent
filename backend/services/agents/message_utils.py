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
    """Convert Anthropic-style tool schema to OpenAI Chat Completions format."""
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
        },
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
    """
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
                if btype == "text":
                    txt = (block.get("text") or "").strip()
                    if txt:
                        text_parts.append(txt)
                elif btype == "tool_use":
                    try:
                        args_json = json.dumps(block.get("input") or {})
                    except Exception:
                        args_json = "{}"
                    tool_calls.append({
                        "id": block.get("id") or "",
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
