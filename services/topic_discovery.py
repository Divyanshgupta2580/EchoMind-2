"""
Live Topic Discovery Engine.

Discovers fresh candidate topics exclusively from live information sources:
1. Live Web Search via OpenRouter native web plugin
2. LLM-powered extraction of structured candidates from raw search results
3. Multi-query domain exploration to produce diverse candidates

IMPORTANT: No static fallback pools. If live search is unreachable,
the discovery cycle is skipped with a WARNING log and retried next interval.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from services.llm import LLMClient
from tools.shared.web_search import web_search
from services.memory import AgentMemoryStore

logger = logging.getLogger(__name__)


class TopicDiscoveryService:
    """
    Autonomous candidate topic discovery service.
    Queries live web sources only — no hardcoded or static fallback data.
    """

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm = llm_client or LLMClient()

    async def discover_candidate_topics(
        self,
        persona_domain: str,
        recent_hashes: set[str] | None = None
    ) -> list[dict[str, Any]]:
        """
        Discover 3-5 candidate topics for editorial evaluation.

        Uses live web search exclusively. If the search API is unreachable
        or returns an error, logs a WARNING and returns an empty list so
        the publishing cycle is cleanly skipped without faking data.
        """
        candidates: list[dict[str, Any]] = []
        recent_hashes = recent_hashes or set()

        # Query live search — this is the ONLY source of candidates
        try:
            search_query = f"latest {persona_domain} breakthroughs vulnerabilities benchmarks papers 2026"
            search_raw = await web_search(query=search_query)

            if not search_raw or "Error:" in search_raw:
                logger.warning(
                    f"[DISCOVERY] Live web search failed or returned error for '{persona_domain}': "
                    f"{search_raw or 'empty response'}. Skipping this discovery cycle."
                )
                return []

            logger.info(f"[DISCOVERY] Live web search succeeded for '{persona_domain}'")

            # Parse structured topics from search results using LLM
            search_candidates = await self._extract_candidates_from_search(
                search_raw, persona_domain
            )

            if not search_candidates:
                logger.warning(
                    f"[DISCOVERY] LLM could not extract any candidates from live search results "
                    f"for '{persona_domain}'. Skipping this discovery cycle."
                )
                return []

            candidates.extend(search_candidates)

        except Exception as e:
            logger.warning(
                f"[DISCOVERY] Live web search encountered an unrecoverable exception for "
                f"'{persona_domain}': {e}. Skipping this discovery cycle — will retry next interval."
            )
            return []

        # Deduplicate by title
        seen = set()
        unique_candidates = []
        for c in candidates:
            if c["title"] not in seen:
                seen.add(c["title"])
                unique_candidates.append(c)

        logger.info(f"[DISCOVERY] Discovered {len(unique_candidates)} unique live candidate topics for '{persona_domain}'")
        return unique_candidates[:5]

    async def _extract_candidates_from_search(
        self,
        search_text: str,
        persona_domain: str
    ) -> list[dict[str, Any]]:
        """Use LLM to extract clean topic candidates from live search raw text with strict source URL tracking."""
        # Pre-extract all HTTP/HTTPS URLs present in the search text
        all_discovered_urls = []
        for u in re.findall(r'https?://[^\s)\]">]+', search_text):
            clean_u = u.rstrip(".,;:)")
            if clean_u not in all_discovered_urls:
                all_discovered_urls.append(clean_u)

        schema = {
            "type": "json_schema",
            "json_schema": {
                "name": "topic_extraction",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "topics": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {
                                        "type": "string",
                                        "description": "Specific technical headline of the discovery or news item."
                                    },
                                    "summary": {
                                        "type": "string",
                                        "description": "Detailed factual technical summary of what was released, discovered, or benchmarked."
                                    },
                                    "source_urls": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "The exact HTTP/HTTPS source URLs where this information was discovered."
                                    },
                                    "domain_relevance": {
                                        "type": "string",
                                        "description": "Explanation of how this relates directly to the technical domain."
                                    }
                                },
                                "required": ["title", "summary", "source_urls", "domain_relevance"],
                                "additionalProperties": False
                            }
                        }
                    },
                    "required": ["topics"],
                    "additionalProperties": False
                }
            }
        }

        system_prompt = (
            "You are an autonomous technical research analyst. Your job is to extract verified, "
            "factual technical news topics from live search results.\n\n"
            "CRITICAL REQUIREMENT:\n"
            "You MUST extract the exact HTTP/HTTPS source URLs from the search results and assign them "
            "to 'source_urls' for each candidate topic. Under NO circumstances return an empty source_urls array."
        )

        user_prompt = f"""Extract 2-3 distinct technical candidate topics from these live search results for domain '{persona_domain}'.

Search results:
{search_text[:3500]}
"""

        try:
            result = await self.llm.generate_structured(
                system=system_prompt,
                user=user_prompt,
                response_format=schema
            )
            extracted = []
            now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            for t in result.get("topics", []):
                # Clean and validate source URLs
                raw_sources = t.get("source_urls", [])
                if isinstance(raw_sources, str):
                    raw_sources = [raw_sources]

                valid_sources = [
                    str(s).strip() for s in raw_sources
                    if isinstance(s, str) and str(s).strip().startswith("http")
                ]

                # If LLM omitted URLs, backfill from discovered search URLs
                if not valid_sources and all_discovered_urls:
                    valid_sources = all_discovered_urls[:2]

                # Deduplicate sources while preserving order
                clean_sources = list(dict.fromkeys(valid_sources))

                extracted.append({
                    "title": t["title"].strip(),
                    "summary": t["summary"].strip(),
                    "source_urls": clean_sources,
                    "domain_relevance": t.get("domain_relevance", f"Relevant to {persona_domain}").strip(),
                    "topic_hash": AgentMemoryStore.compute_topic_hash(t["title"]),
                    "discovered_at": now_utc
                })
            return extracted
        except Exception as e:
            logger.warning(f"[DISCOVERY] Error extracting topics with LLM: {e}")
            return []
