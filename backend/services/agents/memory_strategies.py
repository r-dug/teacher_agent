"""
Memory strategies for the teaching agent's LLM context window.

Each strategy is a function that takes the full message history and returns
the messages to send to the LLM. The system prompt (section content, checklist,
tools) is handled separately and always sent.

Strategies:
  A. turn_summaries — deterministic summaries of recent turns + last 2 messages
  B. full_history_stripped — all messages with base64 images replaced
  C. rag_semantic — top-K retrieved messages by embedding similarity
  D. sliding_window_distilled — distilled summary of old turns + recent window
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_IMAGE_PLACEHOLDER = "[image previously submitted]"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _strip_images(messages: list[dict], keep_last: int = 2) -> list[dict]:
    """Return a copy with base64 images removed from all but the last N messages."""
    if len(messages) <= keep_last:
        return messages
    result: list[dict] = []
    cutoff = len(messages) - keep_last
    for i, msg in enumerate(messages):
        if i >= cutoff:
            result.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            result.append(msg)
            continue
        stripped = []
        had_image = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image":
                had_image = True
                continue
            if isinstance(block, dict) and block.get("type") == "tool_result":
                inner = block.get("content")
                if isinstance(inner, list) and any(
                    isinstance(b, dict) and b.get("type") == "image" for b in inner
                ):
                    had_image = True
                    block = {**block, "content": _IMAGE_PLACEHOLDER}
            stripped.append(block)
        if had_image:
            stripped.insert(0, {"type": "text", "text": _IMAGE_PLACEHOLDER})
        result.append({**msg, "content": stripped})
    return result


def _extract_text(content) -> str:
    """Extract plain text from a message content (str or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    inner = block.get("content", "")
                    if isinstance(inner, str):
                        parts.append(inner)
        return " ".join(parts)
    return str(content)


# ── Turn Summary Tracker ─────────────────────────────────────────────────────

@dataclass
class TurnSummaryTracker:
    """Deterministic turn-level summaries built from tool calls and text."""
    summaries: list[str] = field(default_factory=list)

    def record_tool(self, tool_name: str, tool_input: dict, result_text: str = "") -> None:
        """Record a tool call as a summary line."""
        if tool_name == "mark_task_complete":
            concept = tool_input.get("evidence", "")[:60]
            idx = tool_input.get("task_idx", "?")
            self.summaries.append(f"Marked task {idx} complete: {concept}")
        elif tool_name == "unmark_task":
            reason = tool_input.get("reason", "")[:60]
            idx = tool_input.get("task_idx", "?")
            self.summaries.append(f"Unmarked task {idx}: {reason}")
        elif tool_name == "open_sketchpad":
            prompt = tool_input.get("prompt", "")[:60]
            self.summaries.append(f"Sketchpad: {prompt}")
        elif tool_name == "show_quiz":
            prompt = tool_input.get("prompt", "")[:60]
            self.summaries.append(f"Quiz: {prompt} — {result_text[:20]}")
        elif tool_name == "fill_in_the_blank":
            self.summaries.append(f"Fill-in-blank exercise — {result_text[:40]}")
        elif tool_name == "show_flashcard_deck":
            self.summaries.append(f"Flashcard review — {result_text[:40]}")
        elif tool_name == "text_input":
            self.summaries.append(f"Text input exercise — {result_text[:40]}")
        elif tool_name == "search_content":
            query = tool_input.get("query", "")[:40]
            self.summaries.append(f"Searched: {query}")
        elif tool_name == "play_audio_clip":
            text = tool_input.get("text", "")[:40]
            self.summaries.append(f"Audio clip: {text}")
        else:
            self.summaries.append(f"Tool: {tool_name}")

    def record_text(self, text: str) -> None:
        """Record a text-only turn as a summary line."""
        snippet = text.strip()[:80].replace("\n", " ")
        if snippet:
            self.summaries.append(f"Teacher: {snippet}")

    def record_student(self, text: str) -> None:
        """Record student input."""
        snippet = text.strip()[:80].replace("\n", " ")
        if snippet:
            self.summaries.append(f"Student: {snippet}")

    def recent(self, n: int = 5) -> list[str]:
        return self.summaries[-n:]

    def all(self) -> list[str]:
        return list(self.summaries)

    def clear(self) -> None:
        self.summaries.clear()


# ── Strategy A: Turn Summaries ───────────────────────────────────────────────

def strategy_turn_summaries(
    messages: list[dict],
    turn_summaries: list[str] | None = None,
    episode_context: str | None = None,
    **kwargs,
) -> list[dict]:
    """System prompt + deterministic turn summaries + last 2 messages.

    ~3.5K tokens constant. No extra LLM calls.
    """
    llm_messages: list[dict] = []

    # Episode context from prior sections
    if episode_context:
        llm_messages.append({
            "role": "user",
            "content": f"[Student profile from previous sections]\n{episode_context}",
        })
        llm_messages.append({
            "role": "assistant",
            "content": "Understood. I'll adapt my teaching accordingly.",
        })

    # Recent turn summaries
    if turn_summaries:
        recent = turn_summaries[-5:]
        llm_messages.append({
            "role": "user",
            "content": "[Recent conversation]\n" + "\n".join(f"- {s}" for s in recent),
        })
        llm_messages.append({
            "role": "assistant",
            "content": "Got it. Continuing from where we left off.",
        })

    # Current exchange (last 2 messages: assistant tool_call + tool result, or just user message)
    llm_messages.extend(messages[-2:] if len(messages) >= 2 else messages)

    return llm_messages


# ── Strategy B: Full History (Images Stripped) ───────────────────────────────

def strategy_full_history_stripped(
    messages: list[dict],
    **kwargs,
) -> list[dict]:
    """All messages with base64 images replaced by placeholders.

    Growing token count. Relies on provider prefix caching.
    """
    return _strip_images(messages, keep_last=2)


# ── Strategy C: RAG Semantic ────────────────────────────────────────────────

@dataclass
class EmbeddingCache:
    """Cache embeddings keyed by message text hash to avoid re-embedding."""
    _cache: dict[int, list[float]] = field(default_factory=dict)
    _embed_fn: object = None

    def set_embed_fn(self, fn) -> None:
        self._embed_fn = fn

    def embed(self, text: str) -> list[float]:
        key = hash(text)
        if key in self._cache:
            return self._cache[key]
        if not self._embed_fn:
            return []
        result = self._embed_fn(text)
        self._cache[key] = result
        return result

    def clear(self) -> None:
        self._cache.clear()


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def strategy_rag_semantic(
    messages: list[dict],
    embedding_cache: EmbeddingCache | None = None,
    top_k: int = 5,
    **kwargs,
) -> list[dict]:
    """Top-K retrieved messages by semantic similarity + last 2 messages.

    Uses EmbeddingCache to avoid re-embedding old messages on every call.
    Falls back to recency-based window if no embedding function is set.
    """
    if not embedding_cache or not embedding_cache._embed_fn or len(messages) <= top_k + 2:
        return messages[-(top_k + 2):]

    # Query: last user message text
    last_user = None
    for msg in reversed(messages):
        if msg.get("role") == "user":
            last_user = _extract_text(msg.get("content", ""))
            break
    if not last_user or not last_user.strip():
        return messages[-2:]

    query_vec = embedding_cache.embed(last_user)
    if not query_vec:
        return messages[-2:]

    # Score all candidates except last 2
    candidates = messages[:-2]
    scored: list[tuple[float, int]] = []
    for i, msg in enumerate(candidates):
        text = _extract_text(msg.get("content", ""))
        if not text.strip() or len(text) < 5:
            continue
        msg_vec = embedding_cache.embed(text)
        if msg_vec:
            scored.append((_cosine_sim(query_vec, msg_vec), i))

    scored.sort(reverse=True)
    top_indices = sorted([idx for _, idx in scored[:top_k]])

    retrieved = [candidates[i] for i in top_indices]
    retrieved = _strip_images(retrieved, keep_last=0)

    return retrieved + messages[-2:]


# ── Strategy D: Sliding Window + Distilled ───────────────────────────────────

def strategy_sliding_distilled(
    messages: list[dict],
    window_size: int = 6,
    distill_fn=None,
    episode_context: str | None = None,
    **kwargs,
) -> list[dict]:
    """Distilled summary of old turns + full window of recent turns.

    distill_fn: (messages) -> str  (summarizes a list of messages into text)
    If no distill_fn, falls back to simple truncation of old messages.
    """
    llm_messages: list[dict] = []

    if len(messages) <= window_size:
        return messages

    old_messages = messages[:-window_size]
    recent_messages = messages[-window_size:]

    # Distill old messages
    if distill_fn:
        summary = distill_fn(old_messages)
    else:
        # Fallback: extract text snippets
        parts = []
        for msg in old_messages:
            role = msg.get("role", "")
            text = _extract_text(msg.get("content", ""))[:100]
            if text.strip():
                parts.append(f"{role}: {text}")
        summary = "\n".join(parts[-10:])  # last 10 exchanges

    if episode_context:
        summary = f"[Prior sections] {episode_context}\n\n[Earlier this section]\n{summary}"

    llm_messages.append({
        "role": "user",
        "content": f"[Conversation so far]\n{summary}",
    })
    llm_messages.append({
        "role": "assistant",
        "content": "Understood. Continuing the lesson.",
    })

    # Recent window (images stripped except last 2)
    llm_messages.extend(_strip_images(recent_messages, keep_last=2))

    return llm_messages


# ── Strategy registry ────────────────────────────────────────────────────────

STRATEGIES = {
    "turn_summaries": strategy_turn_summaries,
    "full_history_stripped": strategy_full_history_stripped,
    "rag_semantic": strategy_rag_semantic,
    "sliding_distilled": strategy_sliding_distilled,
}
