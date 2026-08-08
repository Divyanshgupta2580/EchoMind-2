"""
Autonomous Persona Publishing Service.

Orchestrates autonomous topic discovery, editorial rejection, and feed publishing:
1. Loads agent profile & memory context (topic fingerprints, recent coverage).
2. Discovers fresh candidate topics from live web search and domain discovery pools.
3. Evaluates candidate topics against rejection criteria and logs all editorial decisions.
4. Publishes accepted posts to the agent feed with transparent rationale and sources.
5. Runs continuously in background on scheduled intervals without human input.
"""

import asyncio
import logging
from typing import Any

from config.persona_engine import build_persona_profile
from services.editorial_engine import EditorialEngine
from services.llm import LLMClient
from services.memory import AgentMemoryStore, memory_store
from services.topic_discovery import TopicDiscoveryService

logger = logging.getLogger(__name__)


class AutonomousPublisherService:
    """
    Autonomous publishing orchestrator for AI & technology personas.
    """

    def __init__(self, memory: AgentMemoryStore | None = None, llm: LLMClient | None = None):
        self.memory = memory or memory_store
        self.llm = llm or LLMClient()
        self.discovery = TopicDiscoveryService(self.llm)
        self.editorial = EditorialEngine(self.llm, self.memory)

    async def run_publishing_cycle(self, agent_id: str) -> dict[str, Any]:
        """
        Execute one autonomous publishing cycle for a given agent.
        """
        agent = self.memory.get_agent(agent_id)
        if not agent:
            logger.error(f"[PUBLISHER] Agent {agent_id} not found in memory store.")
            return {"success": False, "error": f"Agent {agent_id} not found"}

        logger.info(f"[PUBLISHER] === Starting autonomous cycle for '{agent['name']}' ({agent['domain']}) ===")

        # Step 1: Build persona profile
        profile = build_persona_profile(agent["name"], agent["domain"])

        # Step 2: Get recent topic fingerprints
        recent_hashes = self.memory.get_recent_topic_hashes(agent_id, limit=50)

        # Step 3: Discover candidate topics
        candidates = await self.discovery.discover_candidate_topics(agent["domain"], recent_hashes)
        logger.info(f"[PUBLISHER] Discovered {len(candidates)} candidate topics")

        # Step 4: Run editorial judgment engine
        published = await self.editorial.evaluate_and_publish(
            agent_id=agent_id,
            persona_profile=profile,
            candidates=candidates,
            recent_hashes=recent_hashes
        )

        if not published:
            logger.warning(f"[PUBLISHER] Editorial engine did not accept any candidates this cycle.")
            return {
                "success": True,
                "action": "no_publish",
                "message": "All candidates were rejected or already covered in memory."
            }

        # Step 5: Save post to memory store
        saved_post = self.memory.save_post(
            agent_id=agent_id,
            text=published["text"],
            rationale=published["rationale"],
            sources=published["sources"],
            topic_hash=published.get("topic_hash")
        )

        logger.info(f"[PUBLISHER] Successfully published post {saved_post['id']} for agent {agent_id}")
        return {
            "success": True,
            "action": "published",
            "post": saved_post
        }

    async def run_all_agents_cycle(self) -> None:
        """
        Cycle through all registered agents and run autonomous publishing.
        """
        agents = self.memory.list_agents()
        logger.info(f"[PUBLISHER] Running periodic background cycle for {len(agents)} active agent(s)")
        for agent in agents:
            try:
                await self.run_publishing_cycle(agent["agentId"])
            except Exception as e:
                logger.error(f"[PUBLISHER] Error in cycle for agent {agent['agentId']}: {e}")


# Global publisher singleton instance
publisher_service = AutonomousPublisherService()
