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
class ModelPricing:
    """Per-million-token pricing in USD.

    All rates are in USD per 1,000,000 tokens — more readable than the
    USD-per-token decimals the previous ``_PRICING`` table used.  Used
    by ``usage_tracker._api_cost`` and by ``TeacherAgent.cache_stats``
    to compute per-session dollar savings from prefix caching.

    ``cost()`` takes ``input_uncached`` (input tokens billed at the
    regular rate, after subtracting cache-read + cache-create portions).
    Call sites are expected to do the subtraction themselves because
    different providers report input_tokens with different semantics;
    see the docstring at ``usage_tracker._api_cost`` for details.
    """

    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0
    cache_read_per_mtok: float = 0.0
    cache_write_per_mtok: float = 0.0

    def cost(
        self,
        input_uncached: int,
        output_tokens: int,
        cache_read: int,
        cache_write: int,
    ) -> float:
        return (
            input_uncached * self.input_per_mtok
            + output_tokens * self.output_per_mtok
            + cache_read * self.cache_read_per_mtok
            + cache_write * self.cache_write_per_mtok
        ) / 1_000_000


@dataclass(frozen=True)
class ModelSpec:
    """Declarative description of one LLM model + how to construct it.

    The model's identity (``name``, ``source``) is paired with capability
    metadata (``modalities``, ``context_window``, etc.), pricing fields
    (used by the usage tracker + cache-savings telemetry), and
    source-specific construction config in ``source_config``.

    ``source_config`` always contains:
      - ``api_key_env``: name of the env var holding the API key (read by
        ``_build_one()`` so the key never lives in the spec directly)
      - Optional ``timeout_s``, ``max_retries``, ``base_url``,
        ``cache_retention`` per source

    ``api_key_env`` is omitted only for sources that don't need a key
    (``ollama``, ``vllm``, ``local``).

    Pricing fields are in USD per million tokens — zero defaults so
    unknown-cost models don't silently report fake savings.  Populated
    on every registered ModelSpec in ``model_chains.py`` from the
    upstream providers' public pricing pages.  Neither OpenAI nor
    Anthropic exposes a programmatic rate API as of April 2026, so the
    numbers are hand-maintained; they should be verified whenever a
    provider announces a pricing change.
    """

    name: str
    source: ModelSource
    modalities: frozenset[Modality]
    context_window: int
    max_output: int = 4096
    supports_tools: bool = True
    supports_prefix_cache: bool = False
    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0
    cache_read_per_mtok: float = 0.0
    cache_write_per_mtok: float = 0.0
    source_config: dict = field(default_factory=dict)

    def pricing(self) -> ModelPricing:
        return ModelPricing(
            input_per_mtok=self.input_per_mtok,
            output_per_mtok=self.output_per_mtok,
            cache_read_per_mtok=self.cache_read_per_mtok,
            cache_write_per_mtok=self.cache_write_per_mtok,
        )


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
        # Cache Plan C2: pass ``cache_retention`` only when the spec
        # declares the model supports prefix caching.  Defensive —
        # keeps the provider from sending a parameter the backend might
        # reject on older or cache-incompatible models.
        cache_retention = (
            cfg.get("cache_retention") if spec.supports_prefix_cache else None
        )
        return OpenAILLMProvider(
            model=spec.name,
            api_key=api_key,
            timeout_seconds=float(cfg.get("timeout_s", 30.0)),
            max_retries=int(cfg.get("max_retries", 1)),
            base_url=cfg.get("base_url"),
            cache_retention=cache_retention,
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
        # Local OpenAI-compatible endpoints typically don't implement the
        # Responses API caching controls, so skip cache_retention for them.
        return OpenAILLMProvider(
            model=spec.name,
            api_key=cfg.get("api_key", "not-required"),
            timeout_seconds=float(cfg.get("timeout_s", 60.0)),
            max_retries=int(cfg.get("max_retries", 0)),
            base_url=base_url,
        )

    raise ValueError(f"Unknown ModelSpec.source: {spec.source!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Pricing lookup (for usage_tracker + cache-savings telemetry)
# ─────────────────────────────────────────────────────────────────────────────


_DEFAULT_PRICING = ModelPricing(
    input_per_mtok=3.0,
    output_per_mtok=15.0,
    cache_read_per_mtok=0.30,
    cache_write_per_mtok=3.75,
)


def _dash_prefix_matches(longer: str, shorter: str) -> bool:
    """Return True if ``shorter`` is a dash-delimited prefix of ``longer``.

    Guards against accidental matches like ``gpt-4o`` matching the prefix
    ``gpt-4`` (the ``o`` after the prefix isn't a dash, so we reject).
    Equal strings also count as a match.
    """
    if longer == shorter:
        return True
    if not longer.startswith(shorter):
        return False
    return longer[len(shorter)] == "-"


def lookup_pricing(model: str) -> ModelPricing | None:
    """Look up ``ModelPricing`` for a model name.

    Walks every ``ModelSpec`` registered in ``model_chains.ALL_MODELS``
    (which includes standalone specs that aren't currently in an active
    chain) and tries, in order:

    1. Exact match on ``spec.name``.
    2. Dash-delimited prefix match in either direction (handles version
       suffixes like ``claude-haiku-4-5-20251001`` ↔ ``claude-haiku-4-5``).
       Among multiple prefix candidates, the longest registered name
       wins (most specific).

    Returns ``None`` for truly unknown models — callers decide whether
    to fall back to ``_DEFAULT_PRICING`` or skip cost tracking for that
    record.
    """
    from .model_chains import ALL_MODELS

    seen: dict[str, ModelSpec] = {}
    for spec in ALL_MODELS:
        seen.setdefault(spec.name, spec)

    if model in seen:
        return seen[model].pricing()

    candidates: list[tuple[int, ModelSpec]] = []
    for spec_name, spec in seen.items():
        if _dash_prefix_matches(model, spec_name) or _dash_prefix_matches(spec_name, model):
            candidates.append((len(spec_name), spec))
    if candidates:
        candidates.sort(key=lambda kv: kv[0], reverse=True)
        return candidates[0][1].pricing()

    return None


# ─────────────────────────────────────────────────────────────────────────────
# TTS / STT spec types and builders
# ─────────────────────────────────────────────────────────────────────────────

TTSSource = Literal["openai", "local"]
STTSource = Literal["openai", "local"]


@dataclass(frozen=True)
class TTSModelSpec:
    """Declarative description of one TTS model.

    Follows the same ``source_config`` pattern as ``ModelSpec`` so
    construction is consistent across LLM / TTS / STT providers.
    """

    name: str
    source: TTSSource
    default_voice: str = ""
    default_speed: float = 1.0
    default_format: str = "wav"
    requires_preprocessing: bool = False
    cost_per_minute_usd: float = 0.0
    source_config: dict = field(default_factory=dict)


@dataclass(frozen=True)
class STTModelSpec:
    """Declarative description of one STT model."""

    name: str
    source: STTSource
    lazy_load: bool = False
    cost_per_minute_usd: float = 0.0
    source_config: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TTSChainSpec:
    """Primary TTS model + ordered fallbacks."""

    role: str = "tts"
    primary: TTSModelSpec = field(default_factory=lambda: TTSModelSpec(name="", source="openai"))
    fallbacks: list[TTSModelSpec] = field(default_factory=list)


@dataclass(frozen=True)
class STTChainSpec:
    """Primary STT model + ordered fallbacks."""

    role: str = "stt"
    primary: STTModelSpec = field(default_factory=lambda: STTModelSpec(name="", source="openai"))
    fallbacks: list[STTModelSpec] = field(default_factory=list)


def build_tts_chain(
    spec: TTSChainSpec,
    *,
    kokoro_pipeline=None,
) -> list:
    """Construct an ordered list of TTS provider instances from a chain spec.

    Returns a list suitable for ``TTSPipeline(providers=...)``.  Each
    spec in ``[primary] + fallbacks`` is built; specs that fail (e.g.
    missing API key) are skipped with a warning.
    """
    providers: list = []
    all_specs = [spec.primary] + list(spec.fallbacks)
    for tts_spec in all_specs:
        try:
            providers.append(_build_one_tts(tts_spec, kokoro_pipeline=kokoro_pipeline))
        except Exception as exc:
            log.warning(
                "[build_tts_chain] skipping %s/%s: %s",
                tts_spec.source, tts_spec.name, exc,
            )
    if not providers:
        raise ValueError("build_tts_chain: no TTS providers could be constructed.")
    return providers


def _build_one_tts(spec: TTSModelSpec, *, kokoro_pipeline=None):
    """Construct one TTS provider from a TTSModelSpec."""
    cfg = spec.source_config

    if spec.source == "openai":
        from ..voice.tts import OpenAITTSProvider

        api_key_env = cfg.get("api_key_env", "OPENAI_API_KEY")
        api_key = os.environ.get(api_key_env, "").strip()
        if not api_key:
            raise ValueError(f"OpenAI TTS requires {api_key_env} to be set.")
        return OpenAITTSProvider(
            api_key=api_key,
            model=spec.name,
            default_voice=spec.default_voice,
            response_format=spec.default_format,
            timeout_seconds=float(cfg.get("timeout_s", 20.0)),
            max_retries=int(cfg.get("max_retries", 1)),
            cost_per_minute_usd=spec.cost_per_minute_usd,
        )

    if spec.source == "local":
        from ..voice.tts import KokoroTTSProvider

        if kokoro_pipeline is None:
            raise ValueError("Kokoro TTS requires a loaded pipeline.")
        return KokoroTTSProvider(
            pipeline=kokoro_pipeline,
            default_voice=spec.default_voice or "af_bella",
        )

    raise ValueError(f"Unknown TTSModelSpec.source: {spec.source!r}")


def build_stt_chain(spec: STTChainSpec) -> list:
    """Construct an ordered list of STT provider instances from a chain spec.

    Returns a list of objects with ``.transcribe()`` and
    ``.transcribe_file()`` methods.
    """
    providers: list = []
    all_specs = [spec.primary] + list(spec.fallbacks)
    for stt_spec in all_specs:
        try:
            providers.append(_build_one_stt(stt_spec))
        except Exception as exc:
            log.warning(
                "[build_stt_chain] skipping %s/%s: %s",
                stt_spec.source, stt_spec.name, exc,
            )
    if not providers:
        raise ValueError("build_stt_chain: no STT providers could be constructed.")
    return providers


def _build_one_stt(spec: STTModelSpec):
    """Construct one STT provider from an STTModelSpec."""
    cfg = spec.source_config

    if spec.source == "openai":
        from ..voice.stt import OpenAISTTProvider

        api_key_env = cfg.get("api_key_env", "OPENAI_API_KEY")
        api_key = os.environ.get(api_key_env, "").strip()
        if not api_key:
            raise ValueError(f"OpenAI STT requires {api_key_env} to be set.")
        return OpenAISTTProvider(
            api_key=api_key,
            model=spec.name,
            timeout_seconds=float(cfg.get("timeout_s", 30.0)),
            max_retries=int(cfg.get("max_retries", 1)),
            cost_per_minute_usd=spec.cost_per_minute_usd,
        )

    if spec.source == "local":
        from ..voice.stt import LocalSTTProvider

        return LocalSTTProvider(
            model_size=cfg.get("model_size", "base"),
            lazy=spec.lazy_load,
        )

    raise ValueError(f"Unknown STTModelSpec.source: {spec.source!r}")
