"""OpenAI LLM provider — non-streaming chat completions via the OpenAI SDK."""

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
    """Calls the OpenAI chat completions endpoint (non-streaming)."""

    def __init__(
        self,
        api_key: str,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        if not (api_key or "").strip():
            raise ValueError("OpenAILLMProvider requires a non-empty api_key.")
        self._client = openai.OpenAI(
            api_key=api_key.strip(),
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

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
        }
        if tools:
            kwargs["tools"] = [_tool_schema_to_openai(t) for t in tools]

        log.info("OpenAILLMProvider.do_turn: posting (model=%s, messages=%d)", model, len(messages))

        response = self._client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        message = choice.message

        content_text = message.content or ""
        content_blocks: list[dict] = []
        if content_text:
            content_blocks.append({"type": "text", "text": content_text})

        for tc in (message.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except Exception:
                args = {}
            content_blocks.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.function.name,
                "input": args,
            })

        usage_raw = response.usage
        cached = 0
        if usage_raw and hasattr(usage_raw, "prompt_tokens_details") and usage_raw.prompt_tokens_details:
            cached = getattr(usage_raw.prompt_tokens_details, "cached_tokens", 0) or 0
        usage_obj = SimpleNamespace(
            input_tokens=usage_raw.prompt_tokens if usage_raw else 0,
            output_tokens=usage_raw.completion_tokens if usage_raw else 0,
            cache_read_input_tokens=cached,
            cache_creation_input_tokens=0,
        )

        # Non-streaming: fire on_text_chunk once with the full text.
        if content_text and on_text_chunk:
            on_text_chunk(content_text)

        tool_use: SimpleNamespace | None = None
        for block in content_blocks:
            if block.get("type") == "tool_use":
                tool_use = SimpleNamespace(
                    type="tool_use",
                    id=block["id"],
                    name=block["name"],
                    input=block["input"],
                )
                break

        return LLMTurnResult(
            content_blocks=content_blocks,
            content_text=content_text,
            tool_use=tool_use,
            usage=usage_obj,
        )
