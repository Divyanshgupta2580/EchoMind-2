"""
Web search tool using OpenRouter's native web search plugin.

Provides real-time web search capability for the agent.
Uses OpenRouter's plugins API with native search engine,
with resilient regex fallback for extracting source URLs.
"""

import logging
import re
from typing import Any

import httpx

from config.models import LLM_MODEL
from utils.api import OPENROUTER_URL, get_openrouter_headers

logger = logging.getLogger(__name__)

# Tool configuration for auto-discovery
TOOL_CONFIG = {
    "name": "web_search",
    "description": "Search the web for current information, news, facts, or any data that might not be in training data",
    "params": {
        "query": {
            "type": "string",
            "description": "The search query",
            "required": True
        }
    }
}


async def web_search(query: str, **kwargs) -> str:
    """
    Search the web using OpenRouter's native web search plugin.

    Args:
        query: Search query string.
        **kwargs: Additional context (twitter, db) - not used here.

    Returns:
        Formatted string with search results and verified sources.
    """
    logger.info(f"[WEB_SEARCH] Starting search: {query}")

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "user", "content": query}
        ],
        "plugins": [
            {
                "id": "web",
                "max_results": 5
            }
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                OPENROUTER_URL,
                headers=get_openrouter_headers(),
                json=payload
            )
            response.raise_for_status()
            data = response.json()

        logger.info(f"[WEB_SEARCH] Response received")

        message = data["choices"][0]["message"]
        content = message.get("content", "")

        # 1. Extract source citations from OpenRouter annotations
        sources: list[str] = []
        for annotation in message.get("annotations", []):
            if annotation.get("type") == "url_citation":
                citation = annotation.get("url_citation", {})
                url = citation.get("url") or citation.get("title") or ""
                if url and isinstance(url, str) and url.startswith("http"):
                    if url not in sources:
                        sources.append(url.strip())

        # 2. Extract HTTP/HTTPS URLs from content markdown/text
        urls_in_content = re.findall(r'https?://[^\s)\]">]+', content)
        for u in urls_in_content:
            clean_u = u.rstrip(".,;:")
            if clean_u.startswith("http") and clean_u not in sources:
                sources.append(clean_u)

        logger.info(f"[WEB_SEARCH] Search completed: {len(sources)} unique source URL(s) extracted")

        # Format output with clear demarcation of text and discovered URLs
        sources_block = "\n".join([f"- {s}" for s in sources]) if sources else "- (No external URLs extracted)"
        return (
            f"=== LIVE SEARCH RESULTS ===\n"
            f"{content}\n\n"
            f"=== EXTRACTED SOURCE URLS ===\n"
            f"{sources_block}\n"
        )

    except httpx.TimeoutException:
        logger.error(f"[WEB_SEARCH] Timeout after 60s")
        return "Error: Search timed out"
    except httpx.HTTPStatusError as e:
        logger.error(f"[WEB_SEARCH] API error: {e.response.status_code}")
        return f"Error: Search failed (HTTP {e.response.status_code})"
    except Exception as e:
        logger.error(f"[WEB_SEARCH] Unexpected error: {e}")
        return f"Error: Search failed - {e}"
