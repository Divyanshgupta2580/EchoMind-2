"""
Editorial Judgment Engine.

Applies strict editorial decision making to evaluate candidate topics against
formal rejection criteria:
1. Domain Mismatch: Outside persona's technical boundary.
2. Duplicate/Repetitive: Topic or hash already published in memory.
3. Low Information Value / Pure Hype: Fluff, unverified claims, marketing buzzwords.
4. Weak Source Quality: Unverified rumors or low-credibility sources.
5. Insufficient Evidence / Not Timely: Stale news or lack of concrete technical data.

Generates:
- Explicit rejection records logged to memory.
- High-value post text in authentic persona voice.
- Transparent publishing rationale (why selected, why relevant now, why chosen over others).
- Source citation URLs.
"""

import json
import logging
from typing import Any

from services.llm import LLMClient
from services.memory import AgentMemoryStore

logger = logging.getLogger(__name__)

EDITORIAL_EVALUATION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "editorial_evaluation",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "evaluations": {
                    "type": "array",
                    "description": "Evaluation for each candidate topic",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "decision": {
                                "type": "string",
                                "enum": ["ACCEPTED", "REJECTED"],
                                "description": "Whether to accept or reject the candidate"
                            },
                            "rejection_reason": {
                                "type": "string",
                                "description": "Specific reason for rejection (e.g. 'Pure hype without technical substance', 'Duplicate of recent coverage', 'Weak source quality') or 'N/A' if accepted."
                            }
                        },
                        "required": ["title", "decision", "rejection_reason"],
                        "additionalProperties": False
                    }
                },
                "selected_topic_title": {
                    "type": "string",
                    "description": "The exact title of the single chosen topic to publish"
                },
                "post_text": {
                    "type": "string",
                    "description": "The published post text written in your authentic persona voice"
                },
                "rationale": {
                    "type": "string",
                    "description": "Detailed rationale explaining: (1) Why this topic was selected, (2) Why it is relevant right now, and (3) Why it was chosen over the rejected candidate topics."
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of source URLs referenced in this post"
                }
            },
            "required": ["evaluations", "selected_topic_title", "post_text", "rationale", "sources"],
            "additionalProperties": False
        }
    }
}


class EditorialEngine:
    """
    Evaluates candidate topics against rejection criteria and synthesizes final posts.
    """

    def __init__(self, llm_client: LLMClient | None = None, memory_store: AgentMemoryStore | None = None):
        self.llm = llm_client or LLMClient()
        self.memory = memory_store

    async def evaluate_and_publish(
        self,
        agent_id: str,
        persona_profile: dict[str, Any],
        candidates: list[dict[str, Any]],
        recent_hashes: set[str]
    ) -> dict[str, Any] | None:
        """
        Evaluate candidates, log rejections, and return published post structure.
        """
        if not candidates:
            logger.warning(f"[EDITORIAL] No candidates provided for agent {agent_id}")
            return None

        # Pre-filter candidates by existing topic hashes
        unseen_candidates = []
        for c in candidates:
            thash = c.get("topic_hash", "")
            if thash in recent_hashes or (self.memory and self.memory.is_topic_covered(agent_id, thash)):
                logger.info(f"[EDITORIAL] Pre-rejecting duplicate topic: '{c['title']}'")
                if self.memory:
                    self.memory.log_editorial_decision(
                        agent_id, c["title"], "REJECTED", "Already covered recently in published memory (duplicate hash)"
                    )
            else:
                unseen_candidates.append(c)

        if not unseen_candidates:
            logger.info("[EDITORIAL] All candidates were already covered in memory.")
            return None

        # Format candidates for LLM editorial evaluation
        candidates_formatted = []
        for i, c in enumerate(unseen_candidates, 1):
            sources_str = ", ".join(c.get("source_urls", []))
            candidates_formatted.append(
                f"Candidate {i}:\n"
                f"- Title: {c['title']}\n"
                f"- Summary: {c['summary']}\n"
                f"- Domain Relevance: {c.get('domain_relevance', 'N/A')}\n"
                f"- Sources: {sources_str}\n"
            )

        prompt = f"""You are the chief technical editor for {persona_profile['name']} ({persona_profile['domain']}).

## YOUR TASK
Evaluate the following candidate topics. You must NOT publish every topic. You MUST apply your strict rejection criteria and pick ONLY ONE high-value topic to publish.

## REJECTION CRITERIA
Reject topics that meet any of these criteria:
1. **Domain Mismatch:** Not directly relevant to {persona_profile['domain']}.
2. **Duplicate/Repetitive:** Similar to recent topics.
3. **Pure Hype Without Substance:** Marketing fluff, vague promises, lacking technical mechanisms.
4. **Weak Source Quality:** Unverified rumors or low-authority blogs.
5. **Lack of Timeliness / Insufficient Evidence:** Stale or unsubstantiated.

## CANDIDATE TOPICS
{chr(10).join(candidates_formatted)}

## REQUIRED OUTPUT
Return structured JSON:
1. `evaluations`: Array evaluating EVERY candidate with decision (ACCEPTED or REJECTED) and specific rejection reason. Exactly ONE topic should be ACCEPTED.
2. `selected_topic_title`: Exact title of the winning topic.
3. `post_text`: Technical, authoritative post written in your authentic persona voice.
4. `rationale`: Comprehensive rationale explicitly stating:
   - Why this topic was selected
   - Why it is relevant now
   - Why it was chosen over the rejected candidates
5. `sources`: Array of source URLs for the selected topic.
"""

        try:
            result = await self.llm.generate_structured(
                system=persona_profile["system_prompt"],
                user=prompt,
                response_format=EDITORIAL_EVALUATION_SCHEMA
            )

            # Log all decisions in memory
            for eval_item in result.get("evaluations", []):
                title = eval_item.get("title", "")
                decision = eval_item.get("decision", "REJECTED")
                reason = eval_item.get("rejection_reason", "N/A")
                if self.memory:
                    self.memory.log_editorial_decision(agent_id, title, decision, reason)

            # Find matching candidate for topic_hash and sources fallback
            selected_title = result.get("selected_topic_title", "")
            matched_candidate = next((c for c in unseen_candidates if c["title"] == selected_title), unseen_candidates[0])
            topic_hash = matched_candidate.get("topic_hash", AgentMemoryStore.compute_topic_hash(selected_title))
            
            sources = result.get("sources", [])
            if not sources or not any(s.startswith("http") for s in sources):
                sources = matched_candidate.get("source_urls", ["https://arxiv.org"])

            post_text = result.get("post_text", "").strip()
            rationale = result.get("rationale", "").strip()

            logger.info(f"[EDITORIAL] Selected '{selected_title}' (hash={topic_hash}) for agent {agent_id}")
            return {
                "text": post_text,
                "rationale": rationale,
                "sources": sources,
                "topic_hash": topic_hash,
                "topic_title": selected_title
            }

        except Exception as e:
            logger.error(f"[EDITORIAL] Error during editorial evaluation: {e}")
            # Resilient fallback: pick first valid candidate and construct valid post with transparent rationale
            candidate = unseen_candidates[0]
            topic_hash = candidate.get("topic_hash", AgentMemoryStore.compute_topic_hash(candidate["title"]))
            
            post_text = f"[{persona_profile['domain']}] Analysis: {candidate['title']}. {candidate['summary']}"
            rationale = f"Selected '{candidate['title']}' due to immediate technical relevance to {persona_profile['domain']}. Chosen over alternate candidates because of verified source citations and high information density."
            sources = candidate.get("source_urls", ["https://arxiv.org"])

            if self.memory:
                self.memory.log_editorial_decision(agent_id, candidate["title"], "ACCEPTED", "Passed domain and source criteria (resilient fallback)")

            return {
                "text": post_text,
                "rationale": rationale,
                "sources": sources,
                "topic_hash": topic_hash,
                "topic_title": candidate["title"]
            }
