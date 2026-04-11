"""
Declarative LLM model + chain configuration.

Plan B follow-up A: replaces the branchy ``if provider == "openai"`` /
``if openai_key`` construction logic in ``AgentSession.__init__`` (and
the now-deleted ``LessonPlannerAgent.__init__``) with a single
declarative tree.

A ``ModelSpec`` describes one model + the source-specific config needed
to construct an SDK client for it.  A ``ChainSpec`` is a primary model
plus an ordered list of fallbacks for a particular role
(``teach``, ``decompose``, ``search``, ``embed``, ``tts_prep``,
``instructions``, ``advisor``, ``objectives``, ``ocr``, ``utility``).

``build_chain(spec)`` is the single source of truth for constructing
``LLMProvider`` instances — all of the codebase's call sites use it.
Concrete chain instances live in ``model_chains.py``.

Adding a new model source (Ollama, vLLM, local fine-tunes) means
extending ``_build_one()`` with one more branch and adding a
``ModelSpec`` for it.  No call site changes needed.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .providers.base import LLMProvider

log = logging.getLogger(__name__)


ModelSource = Literal["anthropic", "openai", "ollama", "vllm", "local"]
Modality = Literal["text", "vision", "audio_in", "audio_out"]


@dataclass(frozen=True)
class ModelSpec:
    """Declarative description of one LLM model + how to construct it.

    The model's identity (``name``, ``source``) is paired with capability
    metadata (``modalities``, ``context_window``, etc.) and source-specific
    construction config in ``source_config``.

    ``source_config`` always contains:
      - ``api_key_env``: name of the env var holding the API key (read by
        ``_build_one()`` so the key never lives in the spec directly)
      - Optional ``timeout_s``, ``max_retries``, ``base_url`` per source

    ``api_key_env`` is omitted only for sources that don't need a key
    (``ollama``, ``vllm``, ``local``).
    """

    name: str
    source: ModelSource
    modalities: frozenset[Modality]
    context_window: int
    max_output: int = 4096
    supports_tools: bool = True
    supports_prefix_cache: bool = False
    source_config: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ChainSpec:
    """A primary model + ordered list of fallbacks for one role.

    On ``build_chain(spec)``, providers are constructed for the primary
    and each fallback.  If only one provider is built, it's returned bare;
    otherwise it's wrapped in ``FallbackLLMProvider`` so the caller doesn't
    need to know whether fallbacks exist.
    """

    role: str
    primary: ModelSpec
    fallbacks: list[ModelSpec] = field(default_factory=list)


def build_chain(spec: ChainSpec) -> "LLMProvider":
    """Construct an LLMProvider chain from a ChainSpec.

    Single source of truth: this is the only function that calls
    ``AnthropicLLMProvider(...)`` or ``OpenAILLMProvider(...)`` directly
    (outside the providers' own internals).  Every call site asks for a
    chain by role and uses the returned LLMProvider via ``do_turn()`` or
    ``complete()``.

    Returns the bare primary provider when no fallbacks are configured;
    otherwise wraps in ``FallbackLLMProvider``.
    """
    from .providers.fallback import FallbackLLMProvider

    primary = _build_one(spec.primary)
    if not spec.fallbacks:
        return primary

    chain: list[tuple] = [(primary, spec.primary.name)]
    for fb_spec in spec.fallbacks:
        try:
            chain.append((_build_one(fb_spec), fb_spec.name))
        except Exception as exc:
            log.warning(
                "[build_chain] skipping fallback %s/%s (%s)",
                fb_spec.source, fb_spec.name, exc,
            )
    if len(chain) == 1:
        return chain[0][0]
    return FallbackLLMProvider(chain)


def _build_one(spec: ModelSpec) -> "LLMProvider":
    """Construct one provider instance from a ModelSpec.

    Reads ``source_config["api_key_env"]`` from the environment for
    sources that need a key (anthropic, openai).  Raises ``ValueError``
    if the env var is missing — the chain builder catches this for
    fallbacks but lets it propagate for the primary so the caller
    notices a misconfigured chain at startup.
    """
    cfg = spec.source_config
    if spec.source == "anthropic":
        from .providers.anthropic import AnthropicLLMProvider

        api_key_env = cfg.get("api_key_env", "ANTHROPIC_API_KEY")
        api_key = os.environ.get(api_key_env, "").strip()
        if not api_key:
            raise ValueError(
                f"AnthropicLLMProvider for model {spec.name!r} requires "
                f"environment variable {api_key_env!r} to be set."
            )
        return AnthropicLLMProvider(
            model=spec.name,
            api_key=api_key,
            max_retries=int(cfg.get("max_retries", 6)),
        )

    if spec.source == "openai":
        from .providers.openai import OpenAILLMProvider

        api_key_env = cfg.get("api_key_env", "OPENAI_API_KEY")
        api_key = os.environ.get(api_key_env, "").strip()
        if not api_key:
            raise ValueError(
                f"OpenAILLMProvider for model {spec.name!r} requires "
                f"environment variable {api_key_env!r} to be set."
            )
        return OpenAILLMProvider(
            model=spec.name,
            api_key=api_key,
            timeout_seconds=float(cfg.get("timeout_s", 30.0)),
            max_retries=int(cfg.get("max_retries", 1)),
            base_url=cfg.get("base_url"),
        )

    if spec.source in ("ollama", "vllm"):
        # OpenAI-compatible API at a custom base_url; the api_key is usually
        # not required but the SDK demands a non-empty string.
        from .providers.openai import OpenAILLMProvider

        base_url = cfg.get("base_url")
        if not base_url:
            raise ValueError(
                f"{spec.source} provider for model {spec.name!r} requires "
                f"source_config['base_url'] to be set."
            )
        return OpenAILLMProvider(
            model=spec.name,
            api_key=cfg.get("api_key", "not-required"),
            timeout_seconds=float(cfg.get("timeout_s", 60.0)),
            max_retries=int(cfg.get("max_retries", 0)),
            base_url=base_url,
        )

    raise ValueError(f"Unknown ModelSpec.source: {spec.source!r}")
