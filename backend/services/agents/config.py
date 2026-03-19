"""Agent-level constants."""

SEGMENT_TARGET_PAGES = 25   # ideal page count per parallel analysis segment
MAX_SEGMENT_WORKERS = 8     # max concurrent LLM calls in Phase 2

_HAIKU_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_LLM_MODEL = "claude-sonnet-4-6"
