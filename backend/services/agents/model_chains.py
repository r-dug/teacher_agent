"""
Concrete LLM model + chain definitions.

Plan B follow-up A: typed Python config — module-level ``ChainSpec``
instances per role.  Adding a new model to a role is a single edit
to this file.

**Operational note**: the deployed environment has **zero Anthropic API
credit balance** at the moment, so OpenAI models are the operational
defaults.  Anthropic specs are kept in the chains as fallbacks (or for
future use when credits are topped up) but they're listed AFTER the
OpenAI primary so they only fire if OpenAI fails.

If we want runtime overrides via env vars or per-environment chain
selection (``MODEL_PROFILE=production``), that's deferred — see
"Pydantic / env-var-overridable config flexibility" in the plan TODOs.
For now this file is the source of truth and changing models is a code
edit.
"""

from __future__ import annotations

from .model_config import (
    ChainSpec,
    ModelSpec,
    STTChainSpec,
    STTModelSpec,
    TTSChainSpec,
    TTSModelSpec,
)

# ─────────────────────────────────────────────────────────────────────────────
# Model definitions — referenced by chains below.
# ─────────────────────────────────────────────────────────────────────────────

# OpenAI flagship model — vision, tools, prefix caching, 128K context.
#
# Cache Plan C2: ``cache_retention="24h"`` opts into the Responses API
# extended-retention tier.  Same price as the default 5-10 min window;
# the longer TTL matters for teaching sessions where a student might
# pause for 10-30 min between turns (phone call, bathroom break, etc.)
# without the cached prefix being evicted.
#
# Pricing as of 2026-04-12 from platform.openai.com/docs/pricing.
# OpenAI has no separate cache-write charge — create-writes are free.
GPT_4O = ModelSpec(
    name="gpt-4o",
    source="openai",
    modalities=frozenset({"text", "vision"}),
    context_window=128_000,
    max_output=4_096,
    supports_tools=True,
    supports_prefix_cache=True,
    input_per_mtok=2.50,
    output_per_mtok=10.00,
    cache_read_per_mtok=1.25,
    cache_write_per_mtok=0.0,
    source_config={
        "api_key_env": "OPENAI_API_KEY",
        "timeout_s": 30.0,
        "max_retries": 1,
        "cache_retention": "24h",
    },
)

# Cheaper / longer-context OpenAI option, useful for bulk decompose / OCR.
GPT_4O_MINI = ModelSpec(
    name="gpt-4o-mini",
    source="openai",
    modalities=frozenset({"text", "vision"}),
    context_window=128_000,
    max_output=16_384,
    supports_tools=True,
    supports_prefix_cache=True,
    input_per_mtok=0.15,
    output_per_mtok=0.60,
    cache_read_per_mtok=0.075,
    cache_write_per_mtok=0.0,
    source_config={
        "api_key_env": "OPENAI_API_KEY",
        "timeout_s": 45.0,
        "max_retries": 1,
    },
)

# Anthropic Sonnet — high quality, 200K context, currently NOT usable
# (zero credit balance).  Listed for future use; chains keep it as a
# fallback so topping up credits is a config-only change.
#
# Pricing as of 2026-04-12 from platform.claude.com/docs/en/about-claude/pricing.
# Anthropic charges separately for cache creation (writes).
CLAUDE_SONNET_4_6 = ModelSpec(
    name="claude-sonnet-4-6",
    source="anthropic",
    modalities=frozenset({"text", "vision"}),
    context_window=200_000,
    max_output=8_192,
    supports_tools=True,
    supports_prefix_cache=True,
    input_per_mtok=3.00,
    output_per_mtok=15.00,
    cache_read_per_mtok=0.30,
    cache_write_per_mtok=3.75,
    source_config={
        "api_key_env": "ANTHROPIC_API_KEY",
        "max_retries": 6,
    },
)

# Anthropic Haiku — cheap and fast, currently not usable (zero credits).
CLAUDE_HAIKU_4_5 = ModelSpec(
    name="claude-haiku-4-5-20251001",
    source="anthropic",
    modalities=frozenset({"text", "vision"}),
    context_window=200_000,
    max_output=8_192,
    supports_tools=True,
    supports_prefix_cache=True,
    input_per_mtok=0.25,
    output_per_mtok=1.25,
    cache_read_per_mtok=0.03,
    cache_write_per_mtok=0.30,
    source_config={
        "api_key_env": "ANTHROPIC_API_KEY",
        "max_retries": 3,
    },
)


# ─────────────────────────────────────────────────────────────────────────────
# Chains by role.
# ─────────────────────────────────────────────────────────────────────────────

# Teaching turn — the main agent loop.  Vision needed for sketchpad / photo
# / image submission.  Long context useful but not critical.  GPT-4o is the
# default; Claude Sonnet is the fallback (currently broken — see operational
# note above).
TEACH_CHAIN = ChainSpec(
    role="teach",
    primary=GPT_4O,
    fallbacks=[CLAUDE_SONNET_4_6],
)

# Decomposition — bulk PDF → structured sections.  Long context, vision,
# JSON tool output.  Currently uses gpt-4o pending B2 audit.  See
# `scripts/decompose_model_audit.py` for the audit harness.
DECOMPOSE_CHAIN = ChainSpec(
    role="decompose",
    primary=GPT_4O,
    fallbacks=[CLAUDE_SONNET_4_6],
)

# Web search — uses Responses API's built-in `web_search` tool, so OpenAI
# only.  No fallback because Anthropic's web_search beta is the only
# alternative and it requires Anthropic credits.
SEARCH_CHAIN = ChainSpec(
    role="search",
    primary=GPT_4O,
)

# Embeddings — used by the RAG memory strategy.  OpenAI only for now;
# embedding model name is in source_config["model"] because the
# top-level `name` field is for the chat model.  Note: this is
# a slight cheat — embeddings don't really fit ChainSpec since
# EmbeddingProvider is a different interface.  Kept as a chain for
# uniform configuration access.
TEXT_EMBEDDING_3_SMALL = ModelSpec(
    name="text-embedding-3-small",
    source="openai",
    modalities=frozenset({"text"}),
    context_window=8_191,
    max_output=1_536,  # Embedding dimension
    supports_tools=False,
    supports_prefix_cache=False,
    source_config={
        "api_key_env": "OPENAI_API_KEY",
        "timeout_s": 10.0,
        "max_retries": 1,
    },
)

EMBEDDING_CHAIN = ChainSpec(
    role="embed",
    primary=TEXT_EMBEDDING_3_SMALL,
)

# Persona instructions generation — one-off short text completion.
INSTRUCTIONS_CHAIN = ChainSpec(
    role="instructions",
    primary=GPT_4O_MINI,
)

# TTS prep (IPA annotation for Kokoro) — one-off text completion.
TTS_PREP_CHAIN = ChainSpec(
    role="tts_prep",
    primary=GPT_4O_MINI,
)

# Course authoring advisor chat — short conversational turns.
ADVISOR_CHAIN = ChainSpec(
    role="advisor",
    primary=GPT_4O,
)

# Course authoring objectives extraction — short structured output.
OBJECTIVES_CHAIN = ChainSpec(
    role="objectives",
    primary=GPT_4O,
)

# OCR fallback for image-only PDFs (vision input).
OCR_CHAIN = ChainSpec(
    role="ocr",
    primary=GPT_4O_MINI,
)

# Title generation for outlines — one-off short text completion.
TITLE_CHAIN = ChainSpec(
    role="title",
    primary=GPT_4O_MINI,
)


# Complete catalogue of every registered ModelSpec, whether or not it's
# currently in an active chain.  ``model_config.lookup_pricing()`` walks
# this list so that standalone specs (e.g., CLAUDE_HAIKU_4_5, which is
# defined but not currently referenced by any chain) are still findable
# by the usage tracker when a call comes in for their model name.  Add
# a spec here when you define it at module level above.
ALL_MODELS: list[ModelSpec] = [
    GPT_4O,
    GPT_4O_MINI,
    CLAUDE_SONNET_4_6,
    CLAUDE_HAIKU_4_5,
    TEXT_EMBEDDING_3_SMALL,
]


# Lookup table for `clients.py` and other modules that need to find a
# chain by role name.
ROLE_TO_CHAIN: dict[str, ChainSpec] = {
    "teach": TEACH_CHAIN,
    "decompose": DECOMPOSE_CHAIN,
    "search": SEARCH_CHAIN,
    "embed": EMBEDDING_CHAIN,
    "instructions": INSTRUCTIONS_CHAIN,
    "tts_prep": TTS_PREP_CHAIN,
    "advisor": ADVISOR_CHAIN,
    "objectives": OBJECTIVES_CHAIN,
    "ocr": OCR_CHAIN,
    "title": TITLE_CHAIN,
}


# ─────────────────────────────────────────────────────────────────────────────
# TTS chains
# ─────────────────────────────────────────────────────────────────────────────

# OpenAI TTS — gpt-4o-mini-tts with instructions support, per-minute pricing.
OPENAI_TTS = TTSModelSpec(
    name="gpt-4o-mini-tts",
    source="openai",
    default_voice="alloy",
    default_speed=0.97,
    default_format="opus",
    requires_preprocessing=False,
    cost_per_minute_usd=0.015,
    source_config={
        "api_key_env": "OPENAI_API_KEY",
        "timeout_s": 20.0,
        "max_retries": 1,
    },
)

# Kokoro — local TTS, zero cost, requires IPA preprocessing.
KOKORO_TTS = TTSModelSpec(
    name="kokoro",
    source="local",
    default_voice="af_bella",
    requires_preprocessing=True,
)

# Default TTS chain: OpenAI primary, Kokoro fallback.
TTS_CHAIN = TTSChainSpec(
    primary=OPENAI_TTS,
    fallbacks=[KOKORO_TTS],
)

# Local-only TTS chain (production / no-API-key environments).
TTS_CHAIN_LOCAL = TTSChainSpec(
    primary=KOKORO_TTS,
)


# ─────────────────────────────────────────────────────────────────────────────
# STT chains
# ─────────────────────────────────────────────────────────────────────────────

# OpenAI STT — gpt-4o-mini-transcribe, per-minute pricing.
OPENAI_STT = STTModelSpec(
    name="gpt-4o-mini-transcribe",
    source="openai",
    cost_per_minute_usd=0.006,
    source_config={
        "api_key_env": "OPENAI_API_KEY",
        "timeout_s": 30.0,
        "max_retries": 1,
    },
)

# Local STT — FasterWhisper, lazy-loaded on first transcription.
LOCAL_STT = STTModelSpec(
    name="faster-whisper-base",
    source="local",
    lazy_load=True,
    source_config={"model_size": "base"},
)

# Default STT chain: OpenAI primary, local Whisper fallback.
STT_CHAIN = STTChainSpec(
    primary=OPENAI_STT,
    fallbacks=[LOCAL_STT],
)

# Local-only STT chain.
STT_CHAIN_LOCAL = STTChainSpec(
    primary=LOCAL_STT,
)
