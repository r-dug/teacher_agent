"""
Centralized SDK client factory for non-do_turn use cases.

Plan B follow-up A2: most LLM call sites go through ``LLMProvider.do_turn``
or ``LLMProvider.complete`` (via ``model_config.build_chain``).  But a few
genuinely need raw SDK access for features that don't fit the provider
abstraction:

- The Anthropic ``client.beta.messages.create(...)`` web search beta
  (server-managed tool, no client-side dispatch loop).  Currently no
  consumers because the OpenAI Responses API ``web_search`` built-in
  tool replaces this — but kept for future use.
- Direct ``client.files.create(...)`` for OpenAI's Responses API
  ``input_file`` upload pattern (currently we use inline base64 instead).

These factories return *configured* SDK clients with timeout / retries
read from a ``ChainSpec`` rather than hardcoded magic numbers.  No call
site constructs ``anthropic.Anthropic(max_retries=N)`` or
``openai.OpenAI(...)`` directly anymore.

Usage::

    from .clients import openai_client_for
    client = openai_client_for("search")
    response = client.responses.create(...)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from .model_chains import ROLE_TO_CHAIN

if TYPE_CHECKING:
    import anthropic as _anthropic_typing  # noqa: F401
    import openai as _openai_typing  # noqa: F401

log = logging.getLogger(__name__)


def anthropic_client_for(role: str) -> "_anthropic_typing.Anthropic":
    """Return a configured Anthropic SDK client for *role*.

    Reads ``max_retries`` from the role's primary ``ModelSpec.source_config``.
    Falls back to ``max_retries=6`` if the role isn't an Anthropic role
    (defensive default).

    Raises ``KeyError`` if the role is unknown — this is a programming
    error, not a runtime config issue.
    """
    import anthropic

    spec = ROLE_TO_CHAIN[role].primary
    api_key_env = spec.source_config.get("api_key_env", "ANTHROPIC_API_KEY")
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise ValueError(
            f"anthropic_client_for({role!r}) requires environment variable "
            f"{api_key_env!r} to be set."
        )
    return anthropic.Anthropic(
        api_key=api_key,
        max_retries=int(spec.source_config.get("max_retries", 6)),
    )


def openai_client_for(role: str) -> "_openai_typing.OpenAI":
    """Return a configured OpenAI SDK client for *role*.

    Reads ``timeout_s``, ``max_retries``, and optional ``base_url`` from
    the role's primary ``ModelSpec.source_config``.

    Raises ``KeyError`` if the role is unknown.
    """
    import openai

    spec = ROLE_TO_CHAIN[role].primary
    api_key_env = spec.source_config.get("api_key_env", "OPENAI_API_KEY")
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise ValueError(
            f"openai_client_for({role!r}) requires environment variable "
            f"{api_key_env!r} to be set."
        )
    kwargs: dict = {
        "api_key": api_key,
        "timeout": float(spec.source_config.get("timeout_s", 30.0)),
        "max_retries": int(spec.source_config.get("max_retries", 1)),
    }
    if base_url := spec.source_config.get("base_url"):
        kwargs["base_url"] = base_url
    return openai.OpenAI(**kwargs)
