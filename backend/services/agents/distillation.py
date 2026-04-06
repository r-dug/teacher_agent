"""Live distillation data collection.

Captures complete LLM turns (system prompt, messages, tools, model) to JSONL
for later use in fine-tuning. Only fires when DISTILLATION_COLLECT=true AND
the user has opted in via training_consent preference.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .message_utils import _messages_to_openai, _tool_schema_to_openai
from .tools import TEACHING_TOOLS

log = logging.getLogger(__name__)

OPENAI_TOOLS = [_tool_schema_to_openai(t) for t in TEACHING_TOOLS]
OUTPUT_DIR = Path("storage/distillation")


def make_turn_logger(
    user_has_consent: bool,
    collect_enabled: bool,
):
    """
    Factory that returns an on_turn_logged callback (or None).

    Both conditions must be true for data to be collected:
      - collect_enabled: DISTILLATION_COLLECT env var is true
      - user_has_consent: user.prefs.training_consent is true
    """
    if not collect_enabled or not user_has_consent:
        return None

    def on_turn_logged(
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
        model: str,
    ) -> None:
        try:
            oai_messages = _messages_to_openai(messages)

            record = {
                "ts": time.time(),
                "model": model,
                "system": system_prompt,
                "messages": [{"role": "system", "content": system_prompt}] + oai_messages,
                "tools": OPENAI_TOOLS,
            }

            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            path = OUTPUT_DIR / "live_turns.jsonl"
            with open(path, "a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        except Exception:
            log.debug("Failed to log turn for distillation", exc_info=True)

    return on_turn_logged
