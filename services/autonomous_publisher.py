"""
Autonomous Persona Publishing Service.

Orchestrates the 5-minute continuous discovery loop and the 2-hour quality-driven publishing window:
1. Runs discovery & evaluation approximately every 5 minutes:
   - Discovers news candidates from live sources and tech feeds.
   - Calculates deterministic 0-100 scores across 6 criteria.
   - Persists all candidates, scores, and rejection decisions to SQLite.
   - Dynamically tracks and updates the window's top-scoring leader.
2. At the end of every 2-hour window (or when window ends_at <= now):
   - Compares all eligible candidates in the window.
   - Selects the highest-quality leader.
   - Verifies the leader meets MIN_NEWS_SCORE (default: 75.0).
   - If qualified: publishes exactly ONE post to X/Twitter via IXPublisher and records it to feed.
   - If no candidate meets threshold: publishes NOTHING (status: NO_QUALIFIED_STORY).
   - Closes the window and opens the next 2-hour window.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from config.persona_engine import build_persona_profile
from config.settings import settings
from services.editorial_engine import EditorialEngine
from services.llm import LLMClient
from services.memory import AgentMemoryStore, memory_store
from services.publisher_interface import IXPublisher
from services.topic_discovery import TopicDiscoveryService
from services.twitter import MockXPublisher, XPublisher

logger = logging.getLogger(__name__)


class AutonomousPublisherService:
    """
    Quality-driven autonomous publishing orchestrator.
    """

    def __init__(
        self,
        memory: AgentMemoryStore | None = None,
        llm: LLMClient | None = None,
        publisher: IXPublisher | None = None
    ):
        self.memory = memory or memory_store
        self.llm = llm or LLMClient()
        self.discovery = TopicDiscoveryService(self.llm)
        self.editorial = EditorialEngine(self.llm, self.memory)
        
        # Configure X publisher; default to XPublisher, fallback to MockXPublisher if credentials unset
        if publisher is not None:
            self.publisher = publisher
        elif settings.x_api_key and settings.x_api_secret and settings.x_access_token and settings.x_access_token_secret:
            self.publisher = XPublisher()
        else:
            logger.info("[PUBLISHER] X credentials not detected; using MockXPublisher for safe execution.")
            self.publisher = MockXPublisher()

    async def run_discovery_and_evaluation_cycle(self, agent_id: str) -> dict[str, Any]:
        """
        Execute one 5-minute discovery and evaluation cycle for an agent.
        Does NOT blindly publish; evaluates candidates, updates leader, and checks window status.
        """
        agent = self.memory.get_agent(agent_id)
        if not agent:
            logger.error(f"[PUBLISHER] Agent {agent_id} not found in memory store.")
            return {"success": False, "error": f"Agent {agent_id} not found"}

        # Step 1: Ensure active 2-hour window exists or recover it
        window = self.memory.get_or_create_active_window(agent_id, duration_minutes=settings.publish_window_minutes)
        window_id = window["window_id"]
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        logger.info(f"[WINDOW] Active window {window_id} ({window['started_at']} -> {window['ends_at']}) for agent '{agent['name']}'")

        # Step 2: Check if current window duration has elapsed
        if window["ends_at"] <= now_utc:
            logger.info(f"[WINDOW] Window {window_id} has reached ends_at time. Executing window close evaluation...")
            return await self.process_window_close(agent_id, window_id)

        # Step 3: Run 5-minute candidate discovery
        recent_hashes = self.memory.get_recent_topic_hashes(agent_id, limit=50)
        profile = build_persona_profile(agent["name"], agent["domain"])
        raw_candidates = await self.discovery.discover_candidate_topics(agent["domain"], recent_hashes)
        logger.info(f"[DISCOVERY] Found {len(raw_candidates)} candidate topics for window {window_id}")

        # Step 4: Evaluate and persist each candidate
        evaluated_candidates = []
        current_leader = self.memory.get_current_leader(window_id, min_score=settings.min_news_score)
        leader_score = current_leader["score"] if current_leader else 0.0

        for raw_c in raw_candidates:
            candidate_id = f"c-{uuid.uuid4().hex[:8]}"
            eval_result = await self.editorial.evaluate_candidate(
                agent_id=agent_id,
                persona_profile=profile,
                candidate=raw_c,
                recent_hashes=recent_hashes
            )

            score = eval_result["score"]
            status = eval_result["status"]
            rejection_reason = eval_result["rejection_reason"]
            topic_hash = eval_result["topic_hash"]

            # Save candidate record to SQLite
            saved_c = self.memory.save_candidate(
                candidate_id=candidate_id,
                agent_id=agent_id,
                window_id=window_id,
                title=raw_c["title"],
                summary=raw_c["summary"],
                source_urls=raw_c.get("source_urls", []),
                source_quality=raw_c.get("source_quality", "Unknown"),
                score=score,
                score_breakdown=eval_result["breakdown"],
                status=status,
                rejection_reason=rejection_reason,
                topic_hash=topic_hash
            )
            evaluated_candidates.append(saved_c)

            # Step 5: Update Leader if this eligible candidate beats the previous leader
            if status == "ELIGIBLE" and score > leader_score:
                if current_leader:
                    self.memory.update_candidate_status(current_leader["candidate_id"], "ELIGIBLE")
                    logger.info(f"[LEADER] New candidate {candidate_id} (Score: {score:.1f}) replaced previous leader {current_leader['candidate_id']} (Score: {leader_score:.1f})")
                else:
                    logger.info(f"[LEADER] Candidate {candidate_id} (Score: {score:.1f}) is initial window leader")

                self.memory.update_candidate_status(candidate_id, "LEADER")
                current_leader = saved_c
                leader_score = score

        return {
            "success": True,
            "action": "discovery_cycle_completed",
            "window_id": window_id,
            "candidates_found": len(raw_candidates),
            "current_leader": current_leader
        }

    async def process_window_close(self, agent_id: str, window_id: str) -> dict[str, Any]:
        """
        Execute window close evaluation at the end of the 2-hour publishing window:
        - Selects the highest quality candidate.
        - Publishes exactly ONE post if candidate meets MIN_NEWS_SCORE.
        - Publishes NOTHING if no candidate qualifies.
        - Closes the window and opens the next 2-hour window.
        """
        agent = self.memory.get_agent(agent_id)
        if not agent:
            return {"success": False, "error": f"Agent {agent_id} not found"}

        logger.info(f"[WINDOW] === Closing Window {window_id} for agent '{agent['name']}' ===")
        profile = build_persona_profile(agent["name"], agent["domain"])
        
        # Retrieve best eligible leader in window
        leader = self.memory.get_current_leader(window_id, min_score=settings.min_news_score)

        if not leader:
            logger.info(f"[SELECTION] No candidate met minimum score {settings.min_news_score:.1f}. Publishing NOTHING.")
            self.memory.close_window(window_id=window_id, status="NO_QUALIFIED_STORY")
            new_window = self.memory.create_window(agent_id, duration_minutes=settings.publish_window_minutes)
            return {
                "success": True,
                "action": "no_publication",
                "window_status": "NO_QUALIFIED_STORY",
                "closed_window_id": window_id,
                "next_window_id": new_window["window_id"]
            }

        # Final editorial validation and synthesis
        logger.info(f"[SELECTION] Selected winning candidate: '{leader['title']}' with score={leader['score']:.1f}")
        post_data = await self.editorial.synthesize_post_for_leader(agent_id, profile, leader)

        # Publish to X/Twitter
        logger.info(f"[X] Publishing post for window {window_id} ({len(post_data['text'])} chars)...")
        pub_result = await self.publisher.publish_post(
            text=post_data["text"],
            metadata={
                "window_id": window_id,
                "candidate_id": leader["candidate_id"],
                "topic_hash": post_data["topic_hash"],
                "sources": post_data["sources"]
            }
        )

        if pub_result["success"]:
            # Persist to feed_posts table
            saved_post = self.memory.save_post(
                agent_id=agent_id,
                text=post_data["text"],
                rationale=post_data["rationale"],
                sources=post_data["sources"],
                topic_hash=post_data["topic_hash"],
                post_id=pub_result.get("post_id")
            )
            self.memory.update_candidate_status(leader["candidate_id"], "PUBLISHED")
            self.memory.close_window(
                window_id=window_id,
                status="PUBLISHED",
                selected_candidate_id=leader["candidate_id"],
                post_id=pub_result.get("post_id")
            )
            logger.info(f"[X] Published successfully! Post ID: {pub_result.get('post_id')}")

            # Open next 2-hour window
            new_window = self.memory.create_window(agent_id, duration_minutes=settings.publish_window_minutes)
            return {
                "success": True,
                "action": "published",
                "window_status": "PUBLISHED",
                "post": saved_post,
                "closed_window_id": window_id,
                "next_window_id": new_window["window_id"]
            }
        else:
            logger.error(f"[X] Failed to publish post: {pub_result.get('error')}")
            self.memory.close_window(
                window_id=window_id,
                status="FAILED",
                selected_candidate_id=leader["candidate_id"]
            )
            new_window = self.memory.create_window(agent_id, duration_minutes=settings.publish_window_minutes)
            return {
                "success": False,
                "action": "publish_failed",
                "window_status": "FAILED",
                "error": pub_result.get("error"),
                "closed_window_id": window_id,
                "next_window_id": new_window["window_id"]
            }

    async def run_publishing_cycle(self, agent_id: str) -> dict[str, Any]:
        """Entrypoint for scheduled execution."""
        return await self.run_discovery_and_evaluation_cycle(agent_id)

    async def run_all_agents_cycle(self) -> None:
        """
        Cycle through all registered agents and run continuous discovery & window checks.
        """
        agents = self.memory.list_agents()
        logger.info(f"[PUBLISHER] Running periodic 5-minute background cycle for {len(agents)} active agent(s)")
        for agent in agents:
            try:
                await self.run_discovery_and_evaluation_cycle(agent["agentId"])
            except Exception as e:
                logger.error(f"[PUBLISHER] Error in cycle for agent {agent['agentId']}: {e}")


# Global publisher singleton instance
publisher_service = AutonomousPublisherService()
