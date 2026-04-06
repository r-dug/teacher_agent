"""OpenAI LLM provider — streaming chat completions via the OpenAI SDK."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from types import SimpleNamespace

import openai

from ..message_utils import _messages_to_openai, _tool_schema_to_openai
from .base import LLMProvider, LLMTurnResult

log = logging.getLogger(__name__)


class OpenAILLMProvider(LLMProvider):
    """Calls the OpenAI chat completions endpoint (streaming)."""

    def __init__(
        self,
        api_key: str,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        base_url: str | None = None,
    ) -> None:
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

    @property
    def name(self) -> str:
        return "openai"

    def do_turn(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        on_text_chunk: Callable[[str], None] | None = None,
    ) -> LLMTurnResult:
        kwargs: dict = {
            "model": model,
            "max_completion_tokens": 2048,
            "messages": [{"role": "system", "content": system}] + _messages_to_openai(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = [_tool_schema_to_openai(t) for t in tools]

        log.info("OpenAILLMProvider.do_turn: opening stream (model=%s, messages=%d)", model, len(messages))

        full_text = ""
        tool_calls_raw: dict[int, dict] = {}  # index → accumulated tool call
        usage_raw = None

        with self._client.chat.completions.create(**kwargs) as stream:
            for chunk in stream:
                # Final chunk carries usage when stream_options include_usage=True
                if chunk.usage is not None:
                    usage_raw = chunk.usage

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                # Stream text tokens
                if delta.content:
                    full_text += delta.content
                    if on_text_chunk:
                        on_text_chunk(delta.content)

                # Accumulate tool call deltas
                for tc in (delta.tool_calls or []):
                    entry = tool_calls_raw.setdefault(tc.index, {
                        "id": "", "name": "", "arguments": ""
                    })
                    if tc.id:
                        entry["id"] += tc.id
                    if tc.function:
                        if tc.function.name:
                            entry["name"] += tc.function.name
                        if tc.function.arguments:
                            entry["arguments"] += tc.function.arguments

        # Build content blocks
        content_blocks: list[dict] = []
        if full_text:
            content_blocks.append({"type": "text", "text": full_text})

        tool_use: SimpleNamespace | None = None
        for tc in tool_calls_raw.values():
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except Exception:
                args = {}
            content_blocks.append({
                "type": "tool_use",
                "id": tc["id"],
                "name": tc["name"],
                "input": args,
            })
            if tool_use is None:
                tool_use = SimpleNamespace(
                    type="tool_use",
                    id=tc["id"],
                    name=tc["name"],
                    input=args,
                )

        cached = 0
        if usage_raw and hasattr(usage_raw, "prompt_tokens_details") and usage_raw.prompt_tokens_details:
            cached = getattr(usage_raw.prompt_tokens_details, "cached_tokens", 0) or 0
        usage_obj = SimpleNamespace(
            input_tokens=usage_raw.prompt_tokens if usage_raw else 0,
            output_tokens=usage_raw.completion_tokens if usage_raw else 0,
            cache_read_input_tokens=cached,
            cache_creation_input_tokens=0,
        )

        return LLMTurnResult(
            content_blocks=content_blocks,
            content_text=full_text,
            tool_use=tool_use,
            usage=usage_obj,
        )
