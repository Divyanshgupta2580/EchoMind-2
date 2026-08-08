"""
LLM client for OpenRouter API.

Provides async interface for text generation with structured output support,
multi-turn chat, robust JSON parsing, and fallback model capabilities.
"""

import json
import logging
import re
from typing import Any

import httpx

from config.models import LLM_MODEL
from utils.api import OPENROUTER_URL, get_openrouter_headers

logger = logging.getLogger(__name__)


def _clean_and_parse_json(text: str) -> dict[str, Any]:
    """Clean markdown code fences or prefix/suffix text and parse JSON safely."""
    clean = text.strip()
    if "```json" in clean:
        clean = clean.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in clean:
        clean = clean.split("```", 1)[1].split("```", 1)[0].strip()
    
    # Match outermost JSON object if extra text exists
    match = re.search(r"(\{.*\})", clean, re.DOTALL)
    if match:
        clean = match.group(1)

    return json.loads(clean)


class LLMClient:
    """Async client for OpenRouter LLM API with resilient fallback parsing."""

    def __init__(self, model: str = LLM_MODEL):
        self.model = model

    async def generate(self, system: str, user: str) -> str:
        """Generate text completion."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ]

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                OPENROUTER_URL,
                headers=get_openrouter_headers(),
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": 800
                }
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            return content

    async def generate_structured(
        self,
        system: str,
        user: str,
        response_format: dict[str, Any]
    ) -> dict[str, Any]:
        """Generate structured JSON output with schema enforcement and resilient parsing."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ]

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    OPENROUTER_URL,
                    headers=get_openrouter_headers(),
                    json={
                        "model": self.model,
                        "messages": messages,
                        "max_tokens": 1200,
                        "response_format": response_format
                    }
                )
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                return _clean_and_parse_json(content)
        except Exception as e:
            logger.warning(f"[LLM] Structured generation API call returned error: {e}")
            raise

    async def chat(
        self,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Multi-turn chat completion with optional structured output."""
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 1200
        }

        if response_format:
            payload["response_format"] = response_format

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                OPENROUTER_URL,
                headers=get_openrouter_headers(),
                json=payload
            )
            response.raise_for_status()
            data = response.json()

            content = data["choices"][0]["message"]["content"]
            if response_format:
                return _clean_and_parse_json(content)
            return {"content": content}
