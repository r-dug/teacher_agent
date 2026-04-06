"""Image search via OpenAI Responses API with built-in web_search tool."""

from __future__ import annotations

import logging
import re

import openai

log = logging.getLogger(__name__)

_SEARCH_SYSTEM = (
    "You are an image search assistant. Given a query, use web search to find "
    "a relevant, high-quality image. Return ONLY the direct image URL (ending in "
    ".jpg, .jpeg, .png, .gif, .svg, or .webp). No explanation, no markdown — "
    "just the raw URL on a single line. If you cannot find a suitable image, "
    "respond with exactly: NONE"
)


def search_image_sync(
    query: str,
    api_key: str,
    model: str = "gpt-4o-mini",
    timeout_seconds: float = 15.0,
) -> str | None:
    """Search for an image URL matching the query. Returns the URL or None.

    Uses the OpenAI Responses API with the web_search tool to find images.
    Synchronous — call from a background thread.
    """
    if not query.strip():
        return None

    client = openai.OpenAI(
        api_key=api_key,
        timeout=timeout_seconds,
        max_retries=1,
    )

    try:
        response = client.responses.create(
            model=model,
            instructions=_SEARCH_SYSTEM,
            input=[{"role": "user", "content": f"Find an image: {query}"}],
            tools=[{"type": "web_search_preview"}],
            temperature=0,
        )

        # Extract text from the response output
        text = ""
        for item in response.output:
            if getattr(item, "type", None) == "message":
                for part in getattr(item, "content", []):
                    if getattr(part, "type", None) == "output_text":
                        text += part.text

        text = text.strip()
        if not text or text.upper() == "NONE":
            log.info("[image_search] no result for query=%r", query)
            return None

        # Extract URL from response (model might wrap it in markdown or add text)
        url_match = re.search(r'https?://\S+\.(?:jpg|jpeg|png|gif|svg|webp)(?:\?\S*)?', text, re.IGNORECASE)
        if url_match:
            url = url_match.group(0).rstrip(')')
            log.info("[image_search] query=%r → %s", query, url)
            return url

        # Maybe the whole response is a URL
        if text.startswith("http"):
            log.info("[image_search] query=%r → %s", query, text)
            return text

        log.info("[image_search] no image URL found in response for query=%r: %s", query, text[:200])
        return None

    except Exception as exc:
        log.warning("[image_search] failed for query=%r: %s", query, exc)
        return None