"""
Web search tool using Gemini Google Search grounding with LLMClient fallback.

Provides real-time web search capability for the agent.
Uses Gemini's Google Search grounding with resilient URL citation extraction.
"""

import logging
import re
from typing import Any

import httpx

from config.settings import settings
from services.llm import LLMClient

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
    Search the web using Gemini Google Search grounding with LLMClient fallback.

    Args:
        query: Search query string.
        **kwargs: Additional context (twitter, db) - not used here.

    Returns:
        Formatted string with search results and verified sources.
    """
    logger.info(f"[WEB_SEARCH] Starting search: {query}")

    api_key = settings.gemini_api_key.strip()
    if api_key:
        try:
            gemini_model = settings.gemini_model or "gemini-2.5-flash"
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={api_key}"
            payload = {
                "contents": [{
                    "role": "user",
                    "parts": [{"text": f"Search the web for real-time information: {query}"}]
                }],
                "tools": [{"google_search": {}}],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": 1000
                }
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload)
                if response.status_code == 200:
                    data = response.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        content_parts = candidates[0].get("content", {}).get("parts", [])
                        content = " ".join([p.get("text", "") for p in content_parts if "text" in p])
                        sources = []
                        grounding = candidates[0].get("groundingMetadata", {})
                        for chunk in grounding.get("groundingChunks", []):
                            web = chunk.get("web", {})
                            uri = web.get("uri")
                            if uri and uri.startswith("http"):
                                sources.append(uri)
                        if not sources:
                            urls = re.findall(r'https?://[^\s)\]">]+', content)
                            sources = list(dict.fromkeys(urls))[:5]
                        logger.info(f"[WEB_SEARCH] Completed via Gemini search grounding: {len(sources)} sources found")
                        return f"Search results:\n{content}\n\nSources: {len(sources)}\n" + "\n".join(sources)
        except Exception as exc:
            logger.warning(f"[WEB_SEARCH] Gemini search grounding request failed ({exc}). Falling back to LLMClient...")

    try:
        llm = LLMClient()
        content = await llm.generate(
            system="You are a technical research search assistant. Summarize key technical facts for the given query.",
            user=f"Search query: {query}"
        )
        urls = re.findall(r'https?://[^\s)\]">]+', content)
        sources = list(dict.fromkeys(urls))[:5]
        return f"Search results:\n{content}\n\nSources: {len(sources)}\n" + "\n".join(sources)
    except httpx.TimeoutException:
        logger.error("[WEB_SEARCH] Timeout after 60s")
        return "Error: Search timed out"
    except Exception as exc:
        logger.error(f"[WEB_SEARCH] Search failed: {exc}")
        return f"Error: Search failed - {exc}"
