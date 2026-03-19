def _strip_dangling_tool_use(messages: list[dict]) -> None:
    """
    Remove assistant messages whose tool_use blocks have no matching tool_result
    in the immediately following message.

    Scans the entire list (not just the tail) because the disconnect/save race can
    leave the conversation in various partially-written states.  After removing an
    unmatched assistant message, the orphaned user(tool_result) that may follow it
    is also removed to keep the list in a valid alternating-role state.
    """
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                tool_ids = {
                    b.get("id") for b in content
                    if isinstance(b, dict) and b.get("type") == "tool_use"
                }
                if tool_ids:
                    # Check whether the very next message supplies tool_results for all IDs.
                    next_msg = messages[i + 1] if i + 1 < len(messages) else None
                    next_content = next_msg.get("content", []) if next_msg else []
                    result_ids = {
                        b.get("tool_use_id") for b in (next_content if isinstance(next_content, list) else [])
                        if isinstance(b, dict) and b.get("type") == "tool_result"
                    }
                    if not tool_ids.issubset(result_ids):
                        # Unmatched tool_use — remove this assistant message.
                        messages.pop(i)
                        # Also remove the next message if it's a user(tool_result) — it's orphaned.
                        if i < len(messages):
                            nm = messages[i]
                            nc = nm.get("content", [])
                            if (
                                nm.get("role") == "user"
                                and isinstance(nc, list)
                                and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in nc)
                            ):
                                messages.pop(i)
                        continue  # re-check at same index
        i += 1

    # Pass 2: remove user messages whose tool_result blocks have no matching
    # tool_use in the immediately preceding assistant message.
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "user":
            content = msg.get("content", [])
            if isinstance(content, list):
                result_ids = {
                    b.get("tool_use_id") for b in content
                    if isinstance(b, dict) and b.get("type") == "tool_result"
                }
                if result_ids:
                    prev_msg = messages[i - 1] if i > 0 else None
                    prev_content = prev_msg.get("content", []) if prev_msg else []
                    tool_ids = {
                        b.get("id") for b in (prev_content if isinstance(prev_content, list) else [])
                        if isinstance(b, dict) and b.get("type") == "tool_use"
                    }
                    if not result_ids.issubset(tool_ids):
                        messages.pop(i)
                        continue
        i += 1
