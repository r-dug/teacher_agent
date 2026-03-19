"""Anthropic LLM provider — streaming via client.messages.stream()."""

from __future__ import annotations

import logging
from collections.abc import Callable
from types import SimpleNamespace

from ..message_utils import _block_to_api_dict
from .base import LLMProvider, LLMTurnResult

log = logging.getLogger(__name__)


class AnthropicLLMProvider(LLMProvider):
    """Streams responses from the Anthropic Messages API."""

    def __init__(self, max_retries: int = 6) -> None:
        import anthropic
        self._client = anthropic.Anthropic(max_retries=max_retries)

    @property
    def name(self) -> str:
        return "anthropic"

    def do_turn(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        on_text_chunk: Callable[[str], None] | None = None,
    ) -> LLMTurnResult:
        stream_kwargs: dict = dict(
            model=model,
            max_tokens=2048,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
        )
        if tools:
            stream_kwargs["tools"] = tools

        log.info("AnthropicLLMProvider.do_turn: opening stream (model=%s, messages=%d)", model, len(messages))

        full_text = ""
        with self._client.messages.stream(**stream_kwargs) as stream:
            for chunk in stream.text_stream:
                full_text += chunk
                if on_text_chunk:
                    on_text_chunk(chunk)
            final = stream.get_final_message()

        content_blocks = [_block_to_api_dict(b) for b in final.content]

        tool_use: SimpleNamespace | None = None
        for block in final.content:
            if getattr(block, "type", None) == "tool_use":
                tool_use = block
                break

        return LLMTurnResult(
            content_blocks=content_blocks,
            content_text=full_text,
            tool_use=tool_use,
            usage=final.usage,
        )
