"""OpenAI LLM provider — non-streaming chat completions."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from types import SimpleNamespace

import httpx

from ..message_utils import _format_openai_chat_error, _messages_to_openai, _tool_schema_to_openai
from .base import LLMProvider, LLMTurnResult

log = logging.getLogger(__name__)

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"


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
        self._api_key = api_key
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._max_retries = max(0, int(max_retries))

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
        payload: dict = {
            "model": model,
            "max_tokens": 2048,
            "messages": [{"role": "system", "content": system}] + _messages_to_openai(messages),
        }
        if tools:
            payload["tools"] = [_tool_schema_to_openai(t) for t in tools]

        headers = {"Authorization": f"Bearer {self._api_key}"}

        log.info("OpenAILLMProvider.do_turn: posting (model=%s, messages=%d)", model, len(messages))

        last_err: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                with httpx.Client(timeout=self._timeout_seconds) as client:
                    resp = client.post(_OPENAI_URL, headers=headers, json=payload)
                if resp.status_code >= 400:
                    raise RuntimeError(_format_openai_chat_error(resp))

                data = resp.json()
                choice = (data.get("choices") or [{}])[0]
                message = choice.get("message") or {}

                raw_content = message.get("content") or ""
                if isinstance(raw_content, list):
                    text_parts = []
                    for part in raw_content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            txt = (part.get("text") or "").strip()
                            if txt:
                                text_parts.append(txt)
                    content_text = "\n".join(text_parts).strip()
                else:
                    content_text = str(raw_content).strip()

                content_blocks: list[dict] = []
                if content_text:
                    content_blocks.append({"type": "text", "text": content_text})

                for tc in message.get("tool_calls") or []:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function") or {}
                    args_raw = fn.get("arguments") or "{}"
                    try:
                        parsed_args = (
                            json.loads(args_raw)
                            if isinstance(args_raw, str)
                            else (args_raw if isinstance(args_raw, dict) else {})
                        )
                    except Exception:
                        parsed_args = {}
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id") or "",
                        "name": fn.get("name") or "",
                        "input": parsed_args,
                    })

                usage_raw = data.get("usage") or {}
                usage_obj = SimpleNamespace(
                    input_tokens=int(usage_raw.get("prompt_tokens") or 0),
                    output_tokens=int(usage_raw.get("completion_tokens") or 0),
                    cache_read_input_tokens=int(
                        (usage_raw.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
                    ),
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
                            id=block.get("id", ""),
                            name=block.get("name", ""),
                            input=block.get("input", {}),
                        )
                        break

                return LLMTurnResult(
                    content_blocks=content_blocks,
                    content_text=content_text,
                    tool_use=tool_use,
                    usage=usage_obj,
                )

            except Exception as exc:
                last_err = exc
                if attempt >= self._max_retries:
                    break
                time.sleep(min(0.2 * (2**attempt), 1.0))

        raise RuntimeError(f"OpenAI teaching turn failed: {last_err}") from last_err
