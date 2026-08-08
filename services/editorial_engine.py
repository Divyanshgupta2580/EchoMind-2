"""
Editorial Judgment & Candidate Scoring Engine.

Implements deterministic multi-factor candidate scoring (0-100) and editorial evaluation:
1. Recency: 0-20
2. Significance / Impact: 0-25
3. Persona / Domain Relevance: 0-20
4. Source Quality: 0-15
5. Novelty: 0-10
6. Verifiability: 0-10
Total: 100 points (Threshold: MIN_NEWS_SCORE = 75.0)

Also handles:
- Candidate pre-filtering against existing topic hashes in memory.
- Generation of high-value, authentic post text within 280 characters.
- Generation of 3-part publishing rationale (Why selected, Why relevant now, Why chosen over others).
- Source URL attribution.
"""

import json
import logging
import re
from typing import Any

from config.settings import settings
from services.llm import LLMClient
from services.memory import AgentMemoryStore

logger = logging.getLogger(__name__)

EDITORIAL_SYNTHESIS_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "editorial_post_synthesis",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "post_text": {
                    "type": "string",
                    "description": "The exact post text written in authentic persona voice. MUST be under 280 characters."
                },
                "rationale": {
                    "type": "string",
                    "description": "Transparent rationale explaining: (1) Why selected, (2) Why relevant now, and (3) Why chosen over alternative candidates."
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of source URLs referenced in this post"
                }
            },
            "required": ["post_text", "rationale", "sources"],
            "additionalProperties": False
        }
    }
}


class EditorialEngine:
    """
    Evaluates news candidates, calculates deterministic 0-100 scores,
    and synthesizes verified posts under 280 characters.
    """

    def __init__(self, llm_client: LLMClient | None = None, memory_store: AgentMemoryStore | None = None):
        self.llm = llm_client or LLMClient()
        self.memory = memory_store

    def score_candidate(
        self,
        persona_profile: dict[str, Any],
        candidate: dict[str, Any]
    ) -> tuple[float, dict[str, Any], str | None]:
        """
        Calculate deterministic multi-factor score (0-100) for a news candidate.

        Returns:
            (total_score, score_breakdown, rejection_reason_or_none)
        """
        title = candidate.get("title", "").strip()
        summary = candidate.get("summary", "").strip()
        combined_text = f"{title} {summary}".lower()
        source_urls = candidate.get("source_urls", [])
        if isinstance(source_urls, str):
            source_urls = [source_urls]

        # 1. Recency (0-20)
        recency_score = 18.0
        if any(term in combined_text for term in ["breaking", "just released", "today", "vulnerability disclosure", "cve-"]):
            recency_score = 20.0
        elif any(term in combined_text for term in ["this week", "announces", "release"]):
            recency_score = 16.0

        # 2. Significance / Impact (0-25)
        significance_score = 18.0
        high_impact_keywords = [
            "breakthrough", "benchmark", "quantization", "jailbreak", "adversarial",
            "vulnerability", "cve", "zero-day", "foundational", "state-of-the-art",
            "weights released", "open source", "sub-token", "latency", "exploit"
        ]
        impact_matches = sum(1 for kw in high_impact_keywords if kw in combined_text)
        if impact_matches >= 3:
            significance_score = 24.0
        elif impact_matches >= 1:
            significance_score = 20.0
        elif any(term in combined_text for term in ["rumor", "speculation", "leak", "hype"]):
            significance_score = 8.0

        # 3. Persona / Domain Relevance (0-20)
        domain = persona_profile.get("domain", "").lower()
        domain_score = 14.0
        domain_keywords = {
            "ai security": ["security", "adversarial", "jailbreak", "cve", "vulnerability", "quantization", "bypass", "exploit", "safety", "red-teaming", "prompt injection"],
            "machine learning": ["transformer", "architecture", "training", "inference", "loss", "weights", "dataset", "kv-cache", "attention", "vllm", "decoding"],
            "robotics": ["actuator", "humanoid", "control", "sensor", "teleoperation", "slam", "reinforcement learning", "ros"],
            "ai product": ["adoption", "latency", "cost", "tokens", "enterprise", "api", "infrastructure", "deployment", "pricing"]
        }
        relevant_terms = domain_keywords.get(domain, domain.split())
        domain_matches = sum(1 for term in relevant_terms if term in combined_text)
        if domain_matches >= 2:
            domain_score = 19.0
        elif domain_matches >= 1:
            domain_score = 16.0
        else:
            domain_score = 8.0

        # 4. Source Quality (0-15)
        source_quality_score = 8.0
        high_authority_domains = ["arxiv.org", "cve.mitre.org", "nist.gov", "github.com", "openai.com", "anthropic.com", "huggingface.co", "nature.com"]
        tier1_tech = ["techcrunch.com", "theverge.com", "reuters.com", "wired.com", "venturebeat.com", "arstechnica.com"]
        
        has_high_auth = any(any(auth in str(u).lower() for auth in high_authority_domains) for u in source_urls)
        has_tier1 = any(any(t in str(u).lower() for t in tier1_tech) for u in source_urls)

        if has_high_auth:
            source_quality_score = 14.5
        elif has_tier1:
            source_quality_score = 11.5
        elif len(source_urls) > 0 and source_urls[0].startswith("http"):
            source_quality_score = 9.0
        else:
            source_quality_score = 4.0

        # 5. Novelty (0-10)
        novelty_score = 8.0
        if any(term in combined_text for term in ["novel", "first-ever", "unannounced", "0-day", "new paradigm"]):
            novelty_score = 9.5
        elif any(term in combined_text for term in ["recap", "summary", "best practices", "tutorial"]):
            novelty_score = 4.0

        # 6. Verifiability (0-10)
        verifiability_score = 7.0
        if len(source_urls) >= 2 or has_high_auth:
            verifiability_score = 9.5
        elif len(source_urls) == 1:
            verifiability_score = 7.5
        else:
            verifiability_score = 3.0

        # Total Calculation
        total_score = recency_score + significance_score + domain_score + source_quality_score + novelty_score + verifiability_score
        total_score = min(100.0, max(0.0, total_score))

        breakdown = {
            "recency": recency_score,
            "significance": significance_score,
            "domain_relevance": domain_score,
            "source_quality": source_quality_score,
            "novelty": novelty_score,
            "verifiability": verifiability_score,
            "total": total_score
        }

        min_threshold = settings.min_news_score
        rejection_reason = None
        if total_score < min_threshold:
            rejection_reason = f"Candidate score {total_score:.1f} is below minimum publishing threshold {min_threshold:.1f} (Significance: {significance_score}, Source: {source_quality_score})"

        return total_score, breakdown, rejection_reason

    async def evaluate_candidate(
        self,
        agent_id: str,
        persona_profile: dict[str, Any],
        candidate: dict[str, Any],
        recent_hashes: set[str]
    ) -> dict[str, Any]:
        """
        Evaluate a single candidate, assign status (ELIGIBLE or REJECTED),
        and log decision.
        """
        topic_hash = candidate.get("topic_hash") or self.memory.compute_topic_hash(candidate["title"])
        
        # Check duplication
        if topic_hash in recent_hashes or (self.memory and self.memory.is_candidate_hash_covered(agent_id, topic_hash)):
            reason = "Duplicate content: already covered in recent memory or previous published stories."
            if self.memory:
                self.memory.log_editorial_decision(agent_id, candidate["title"], "REJECTED", reason)
            return {
                "candidate": candidate,
                "score": 0.0,
                "breakdown": {"total": 0.0},
                "status": "REJECTED",
                "rejection_reason": reason,
                "topic_hash": topic_hash
            }

        score, breakdown, rejection_reason = self.score_candidate(persona_profile, candidate)
        status = "ELIGIBLE" if score >= settings.min_news_score else "REJECTED"

        if self.memory:
            decision = "ACCEPTED" if status == "ELIGIBLE" else "REJECTED"
            reason = rejection_reason or f"Meets quality threshold with score {score:.1f}/100"
            self.memory.log_editorial_decision(agent_id, candidate["title"], decision, reason)

        return {
            "candidate": candidate,
            "score": score,
            "breakdown": breakdown,
            "status": status,
            "rejection_reason": rejection_reason,
            "topic_hash": topic_hash
        }

    async def synthesize_post_for_leader(
        self,
        agent_id: str,
        persona_profile: dict[str, Any],
        leader_candidate: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Synthesize authentic X/Twitter post text (<= 280 chars), transparent rationale,
        and source attribution for the selected window leader.
        """
        title = leader_candidate["title"]
        summary = leader_candidate["summary"]
        source_urls = leader_candidate.get("source_urls", [])
        if isinstance(source_urls, str):
            source_urls = [source_urls]

        system_prompt = (
            f"You are {persona_profile['name']}, an autonomous authority in {persona_profile['domain']}.\n"
            f"Editorial stance: {persona_profile.get('editorial_stance', 'Evidence-based, skeptical, technical')}.\n"
            f"Writing style: {persona_profile.get('writing_style', 'Concise, rigorous, technical, no hype')}.\n\n"
            f"CRITICAL CONSTRAINT: You are publishing an official post to X/Twitter. The post_text MUST be under 280 characters.\n"
            f"Explain clearly what happened and why it matters technically without clickbait or emojis."
        )

        user_prompt = (
            f"Synthesize the official post for the selected winning story:\n"
            f"Title: {title}\n"
            f"Summary: {summary}\n"
            f"Primary Sources: {json.dumps(source_urls)}\n"
            f"Candidate Score: {leader_candidate.get('score', 85.0)}\n\n"
            f"Return JSON adhering to schema with:\n"
            f"1. post_text: strictly <= 280 characters, technical, clear, factual.\n"
            f"2. rationale: 3-part rationale (why selected, why relevant now, why chosen over others).\n"
            f"3. sources: array of source URLs."
        )

        try:
            result = await self.llm.generate_structured(
                system=system_prompt,
                user=user_prompt,
                schema=EDITORIAL_SYNTHESIS_SCHEMA
            )
            post_text = result.get("post_text", "").strip()
            # Enforce 280 character limit
            if len(post_text) > 280:
                post_text = post_text[:277] + "..."

            return {
                "text": post_text,
                "rationale": result.get("rationale", f"Selected as window leader with score {leader_candidate.get('score', 85):.1f}."),
                "sources": result.get("sources", source_urls),
                "topic_hash": leader_candidate.get("topic_hash") or self.memory.compute_topic_hash(title)
            }
        except Exception as e:
            logger.warning(f"[EDITORIAL] LLM post synthesis fallback: {e}")
            # Resilient fallback synthesis
            raw_text = f"{title}: {summary}"
            if len(raw_text) > 277:
                raw_text = raw_text[:274] + "..."

            return {
                "text": raw_text,
                "rationale": f"Selected as top-scoring candidate ({leader_candidate.get('score', 85):.1f}/100) meeting all verification criteria for {persona_profile['domain']}.",
                "sources": source_urls,
                "topic_hash": leader_candidate.get("topic_hash") or self.memory.compute_topic_hash(title)
            }

    # Backwards-compatible evaluate_and_publish method
    async def evaluate_and_publish(
        self,
        agent_id: str,
        persona_profile: dict[str, Any],
        candidates: list[dict[str, Any]],
        recent_hashes: set[str]
    ) -> dict[str, Any] | None:
        """Legacy evaluate and publish immediately helper."""
        if not candidates:
            return None

        evaluated = []
        for c in candidates:
            ev = await self.evaluate_candidate(agent_id, persona_profile, c, recent_hashes)
            if ev["status"] == "ELIGIBLE":
                evaluated.append(ev)

        if not evaluated:
            return None

        evaluated.sort(key=lambda x: x["score"], reverse=True)
        top = evaluated[0]["candidate"]
        top["score"] = evaluated[0]["score"]
        top["topic_hash"] = evaluated[0]["topic_hash"]
        return await self.synthesize_post_for_leader(agent_id, persona_profile, top)
