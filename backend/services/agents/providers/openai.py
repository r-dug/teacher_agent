"""OpenAI LLM provider — Responses API (Plan B follow-up A2 migration).

Migrated from Chat Completions API to Responses API in Plan B follow-up
A2.  Responses API is OpenAI's strategic direction for agentic
workloads, supports built-in server-managed tools (``web_search``,
``file_search``, ``code_interpreter``), reasoning models (o1, o3,
gpt-5) with explicit ``reasoning={"effort": ...}`` config, and has
cleaner discrete event types in streams (no more delta reassembly for
tool calls).

The shape of streamed events is different from Chat Completions:
- ``response.output_text.delta`` carries text chunks
- ``response.function_call_arguments.delta`` carries function-call args
- ``response.output_item.done`` fires when a top-level output item
  (text block or function call) finishes
- ``response.completed`` is the final event with usage info

We use stateless Responses API mode — no ``previous_response_id``,
because we own the conversation history client-side (memory strategies,
message stripping, image cleanup).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from types import SimpleNamespace

import openai

from ..message_utils import (
    _messages_to_openai_responses,
    _tool_schema_to_openai_responses,
)
from .base import LLMProvider, LLMTurnResult

log = logging.getLogger(__name__)


class OpenAILLMProvider(LLMProvider):
    """Calls the OpenAI Responses API (streaming).

    Plan B follow-up A: model and config baked into constructor.
    Plan B follow-up A2: migrated from Chat Completions to Responses API.
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        base_url: str | None = None,
        cache_retention: str | None = None,
    ) -> None:
        if not (model or "").strip():
            raise ValueError("OpenAILLMProvider requires a non-empty model.")
        if not (api_key or "").strip():
            raise ValueError("OpenAILLMProvider requires a non-empty api_key.")
        kwargs: dict = {
            "api_key": api_key.strip(),
            "timeout": timeout_seconds,
            "max_retries": max_retries,
        }
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.OpenAI(**kwargs)
        self._base_url = base_url
        self._model = model
        # Cache Plan C2: per-instance retention policy.  None = SDK
        # default (``in_memory``, 5-10 min TTL).  ``"24h"`` opts into
        # the extended retention tier (same price).  Stored as the
        # string the Responses API accepts directly.
        self._cache_retention = cache_retention

    @property
    def name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    def do_turn(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        on_text_chunk: Callable[[str], None] | None = None,
        cache_key: str | None = None,
    ) -> LLMTurnResult:
        effective_model = (model or "").strip() or self._model

        # Translate Anthropic-format messages → Responses API input items.
        input_items = _messages_to_openai_responses(messages)

        # Translate function-tool schemas; built-in tools (web_search etc.)
        # pass through unchanged.
        translated_tools: list[dict] = []
        for t in tools:
            if t.get("type") in ("web_search", "file_search", "code_interpreter", "image_generation"):
                # Built-in Responses API tool — pass through unchanged.
                translated_tools.append(t)
            else:
                translated_tools.append(_tool_schema_to_openai_responses(t))

        kwargs: dict = {
            "model": effective_model,
            "instructions": system,
            "input": input_items,
            "max_output_tokens": 2048,
            "stream": True,
        }
        if translated_tools:
            kwargs["tools"] = translated_tools
        # Cache Plan C2: wire the Responses API caching controls.
        if cache_key:
            kwargs["prompt_cache_key"] = cache_key
        if self._cache_retention:
            kwargs["prompt_cache_retention"] = self._cache_retention

        log.info(
            "OpenAILLMProvider.do_turn: opening Responses stream "
            "(model=%s, input_items=%d, tools=%d)",
            effective_model, len(input_items), len(translated_tools),
        )

        full_text = ""
        # function_call accumulator: call_id → {name, arguments_json, item_id}
        function_calls: dict[str, dict] = {}
        # Track output item types as they appear so we can build content_blocks
        # in the right order at the end.
        output_items_in_order: list[dict] = []
        usage_obj = None

        with self._client.responses.create(**kwargs) as stream:
            for event in stream:
                etype = getattr(event, "type", None)

                if etype == "response.output_text.delta":
                    delta = getattr(event, "delta", "") or ""
                    if delta:
                        full_text += delta
                        if on_text_chunk:
                            on_text_chunk(delta)

                elif etype == "response.function_call_arguments.delta":
                    item_id = getattr(event, "item_id", "")
                    delta = getattr(event, "delta", "") or ""
                    entry = function_calls.setdefault(
                        item_id, {"name": "", "arguments": "", "call_id": ""}
                    )
                    entry["arguments"] += delta

                elif etype == "response.output_item.added":
                    item = getattr(event, "item", None)
                    if item is None:
                        continue
                    item_type = getattr(item, "type", None)
                    if item_type == "function_call":
                        item_id = getattr(item, "id", "")
                        entry = function_calls.setdefault(
                            item_id, {"name": "", "arguments": "", "call_id": ""}
                        )
                        entry["name"] = getattr(item, "name", "") or entry["name"]
                        entry["call_id"] = getattr(item, "call_id", "") or entry["call_id"]

                elif etype == "response.output_item.done":
                    item = getattr(event, "item", None)
                    if item is None:
                        continue
                    item_type = getattr(item, "type", None)
                    if item_type == "function_call":
                        item_id = getattr(item, "id", "")
                        entry = function_calls.get(item_id)
                        if entry is not None:
                            # Make sure we captured name + call_id from the
                            # final item (in case they weren't on the added event).
                            entry["name"] = getattr(item, "name", "") or entry["name"]
                            entry["call_id"] = getattr(item, "call_id", "") or entry["call_id"]
                            output_items_in_order.append({
                                "kind": "function_call",
                                "item_id": item_id,
                            })
                    elif item_type == "message":
                        # Text message item finished — order tracking only.
                        output_items_in_order.append({"kind": "message"})

                elif etype == "response.completed":
                    response = getattr(event, "response", None)
                    if response is not None:
                        usage_obj = getattr(response, "usage", None)

                elif etype == "response.failed":
                    response = getattr(event, "response", None)
                    err = getattr(response, "error", None) if response else None
                    raise RuntimeError(
                        f"OpenAI Responses API failed: {err}"
                    )

        # Build content_blocks in declaration order (text first if present,
        # then tool_use blocks).  We currently use a simplified ordering:
        # text first, then tool_uses.  This matches the Anthropic behavior
        # for our use case (the agent's run_turn loop iterates tool_uses
        # separately and doesn't care about interleaving with text).
        content_blocks: list[dict] = []
        if full_text:
            content_blocks.append({"type": "text", "text": full_text})

        tool_uses: list[SimpleNamespace] = []
        for entry in function_calls.values():
            try:
                args = json.loads(entry["arguments"]) if entry["arguments"] else {}
            except Exception:
                args = {}
            call_id = entry.get("call_id") or ""
            name = entry.get("name") or ""
            content_blocks.append({
                "type": "tool_use",
                "id": call_id,
                "name": name,
                "input": args,
            })
            tool_uses.append(SimpleNamespace(
                type="tool_use",
                id=call_id,
                name=name,
                input=args,
            ))

        # Normalize usage to match the duck-typed shape the rest of the
        # codebase expects (input_tokens, output_tokens, cache_read_input_tokens,
        # cache_creation_input_tokens).
        input_tokens = 0
        output_tokens = 0
        cached = 0
        if usage_obj is not None:
            input_tokens = getattr(usage_obj, "input_tokens", 0) or 0
            output_tokens = getattr(usage_obj, "output_tokens", 0) or 0
            details = getattr(usage_obj, "input_tokens_details", None)
            if details is not None:
                cached = getattr(details, "cached_tokens", 0) or 0

        normalized_usage = SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cached,
            cache_creation_input_tokens=0,
        )

        return LLMTurnResult(
            content_blocks=content_blocks,
            content_text=full_text,
            usage=normalized_usage,
            tool_uses=tool_uses,
        )

    def complete(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int = 1024,
        cache_key: str | None = None,
    ) -> tuple[str, object]:
        """Plan B follow-up A2: non-streaming text completion via Responses API.

        Used by simple text-completion call sites that previously
        constructed ``openai.OpenAI(...)`` inline (or used the httpx
        helper in course_authoring.py).  Returns ``(text, usage)``.
        """
        input_items = _messages_to_openai_responses(messages)
        create_kwargs: dict = {
            "model": self._model,
            "instructions": system,
            "input": input_items,
            "max_output_tokens": max_tokens,
            "stream": False,
        }
        # Cache Plan C2: wire the Responses API caching controls.
        if cache_key:
            create_kwargs["prompt_cache_key"] = cache_key
        if self._cache_retention:
            create_kwargs["prompt_cache_retention"] = self._cache_retention
        response = self._client.responses.create(**create_kwargs)

        # Responses API exposes a convenient `output_text` property that
        # concatenates all output text items.
        text = (getattr(response, "output_text", None) or "").strip()

        # If output_text is missing for some reason, walk the output items.
        if not text:
            parts: list[str] = []
            for item in getattr(response, "output", []) or []:
                if getattr(item, "type", None) == "message":
                    for content_part in getattr(item, "content", []) or []:
                        if getattr(content_part, "type", None) in (
                            "output_text", "text"
                        ):
                            parts.append(getattr(content_part, "text", "") or "")
            text = "\n".join(p for p in parts if p).strip()

        # Normalize usage to match the duck-typed shape.
        usage_raw = getattr(response, "usage", None)
        input_tokens = 0
        output_tokens = 0
        cached = 0
        if usage_raw is not None:
            input_tokens = getattr(usage_raw, "input_tokens", 0) or 0
            output_tokens = getattr(usage_raw, "output_tokens", 0) or 0
            details = getattr(usage_raw, "input_tokens_details", None)
            if details is not None:
                cached = getattr(details, "cached_tokens", 0) or 0

        normalized_usage = SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cached,
            cache_creation_input_tokens=0,
        )
        return text, normalized_usage
