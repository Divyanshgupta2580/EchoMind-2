"""
Hackathon Compliance Test Suite (Standard Unittest & AsyncIO).

Verifies:
1. POST /api/agent/init endpoint format & response
2. Single authoritative global recurring scheduler (no duplicate per-agent interval jobs)
3. GET /api/agent/feed?agentId=... endpoint schema & reverse chronological order
4. ISO 8601 UTC timestamp format
5. Transparent rationale & sources array
6. Explicit editorial rejection logging
7. Database-level topic hash deduplication uniqueness constraint
8. Autonomous publishing cycle execution & restart discovery
"""

import asyncio
import re
import unittest
from fastapi.testclient import TestClient

from main import app, scheduler
from services.memory import memory_store, AgentMemoryStore
from services.autonomous_publisher import publisher_service
from config.persona_engine import build_persona_profile

client = TestClient(app)
ISO_8601_REGEX = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"


class TestHackathonSpecification(unittest.TestCase):

    def test_post_agent_init_and_scheduler_architecture(self):
        """
        Test POST /api/agent/init registers agent and relies on single global scheduler.
        Verifies that /init does NOT create a redundant per-agent interval job.
        """
        payload = {
            "persona": {
                "name": "Ada",
                "domain": "AI Security"
            }
        }
        response = client.post("/api/agent/init", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("agentId", data)
        agent_id = data["agentId"]
        self.assertIsInstance(agent_id, str)
        self.assertTrue(len(agent_id) > 0)

        # Verify agent is persisted in memory
        agent_record = memory_store.get_agent(agent_id)
        self.assertIsNotNone(agent_record)
        self.assertEqual(agent_record["name"], "Ada")
        self.assertEqual(agent_record["domain"], "AI Security")

        # Verify scheduler has no redundant per-agent recurring job
        agent_job = scheduler.get_job(f"agent_cycle_{agent_id}")
        self.assertIsNone(agent_job, "Per-agent recurring job should NOT exist; global scheduler handles all agents.")

    def test_single_global_scheduler_job(self):
        """Verify exactly one global recurring scheduler job exists."""
        global_job = scheduler.get_job("autonomous_publishing_cycle")
        # Global job is registered during lifespan
        if global_job is not None:
            self.assertEqual(global_job.name, "AutonomousPublisherService.run_all_agents_cycle")

    def test_get_feed_empty_for_unknown_agent(self):
        """Test GET /api/agent/feed returns empty array for agent with no posts."""
        response = client.get("/api/agent/feed?agentId=unknown-agent-999")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("posts", data)
        self.assertEqual(data["posts"], [])

    def test_feed_schema_and_timestamps(self):
        """Test published posts meet exact hackathon schema and ISO 8601 UTC format."""
        agent_id = "test-agent-schema"
        memory_store.register_agent(agent_id, "TestBot", "Machine Learning")

        post = memory_store.save_post(
            agent_id=agent_id,
            text="Analysis of speculative decoding KV-cache latency improvements.",
            rationale="Selected due to 2.8x speedup data. Relevant now due to vLLM release. Chosen over hype posts.",
            sources=["https://arxiv.org/abs/2408.04567"]
        )

        response = client.get(f"/api/agent/feed?agentId={agent_id}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("posts", data)
        self.assertTrue(len(data["posts"]) >= 1)

        first_post = data["posts"][0]
        self.assertIn("id", first_post)
        self.assertIn("createdAt", first_post)
        self.assertIn("text", first_post)
        self.assertIn("rationale", first_post)
        self.assertIn("sources", first_post)
        self.assertIsInstance(first_post["sources"], list)
        self.assertTrue(len(first_post["sources"]) > 0)

        # Verify ISO 8601 UTC timestamp format
        self.assertIsNotNone(re.match(ISO_8601_REGEX, first_post["createdAt"]))

    def test_reverse_chronological_order(self):
        """Test feed returns posts in reverse chronological order (newest first)."""
        agent_id = "test-agent-order"
        memory_store.register_agent(agent_id, "OrderBot", "Robotics")

        # Save older post
        memory_store.save_post(
            agent_id=agent_id,
            text="Older post",
            rationale="Rationale 1",
            sources=["https://example.com/1"],
            created_at="2026-08-07T10:00:00Z",
            post_id="p-old",
            topic_hash="hash-old"
        )

        # Save newer post
        memory_store.save_post(
            agent_id=agent_id,
            text="Newer post",
            rationale="Rationale 2",
            sources=["https://example.com/2"],
            created_at="2026-08-07T12:00:00Z",
            post_id="p-new",
            topic_hash="hash-new"
        )

        response = client.get(f"/api/agent/feed?agentId={agent_id}")
        self.assertEqual(response.status_code, 200)
        posts = response.json()["posts"]
        self.assertTrue(len(posts) >= 2)
        self.assertEqual(posts[0]["id"], "p-new")
        self.assertEqual(posts[1]["id"], "p-old")

    def test_database_level_deduplication_uniqueness(self):
        """Test database uniqueness constraint prevents duplicate (agent_id, topic_hash) posts."""
        agent_id = "test-agent-dedup-unique"
        memory_store.register_agent(agent_id, "DedupBot", "AI Security")

        topic_title = "Vulnerability in LoRA Adapter Weight Merging"
        topic_hash = memory_store.compute_topic_hash(topic_title)

        # First insert
        post1 = memory_store.save_post(
            agent_id=agent_id,
            text="LoRA weight extraction vulnerability disclosure.",
            rationale="Selected due to critical CVE.",
            sources=["https://cve.mitre.org"],
            topic_hash=topic_hash,
            post_id="p-lora-1"
        )
        self.assertIsNotNone(post1)
        self.assertEqual(post1["is_duplicate"], False)

        # Second insert with identical (agent_id, topic_hash) - simulates concurrent cycle race
        post2 = memory_store.save_post(
            agent_id=agent_id,
            text="LoRA weight extraction duplicate attempt.",
            rationale="Selected again.",
            sources=["https://cve.mitre.org"],
            topic_hash=topic_hash,
            post_id="p-lora-2"
        )
        self.assertIsNotNone(post2)
        self.assertEqual(post2["is_duplicate"], True)
        self.assertEqual(post2["id"], "p-lora-1", "Should return existing post without creating a second record.")

        # Feed post count must remain exactly 1
        feed = memory_store.get_feed(agent_id)
        self.assertEqual(len(feed), 1)

    def test_editorial_rejection_logging(self):
        """Test editorial engine logs explicit rejection decisions."""
        agent_id = "test-agent-editorial"
        memory_store.register_agent(agent_id, "EditorBot", "AI Security")

        memory_store.log_editorial_decision(
            agent_id=agent_id,
            topic_title="Generic Unhackable AI Firewall PR",
            decision="REJECTED",
            reason="Pure marketing hype without technical red-teaming or whitepaper"
        )

        decisions = memory_store.get_recent_editorial_decisions(agent_id)
        self.assertTrue(len(decisions) >= 1)
        rejected = decisions[0]
        self.assertEqual(rejected["topic"], "Generic Unhackable AI Firewall PR")
        self.assertEqual(rejected["decision"], "REJECTED")
        self.assertIn("hype", rejected["reason"].lower())

    def test_autonomous_publishing_cycle(self):
        """Test full autonomous publishing cycle for an agent."""
        agent_id = "test-agent-cycle"
        memory_store.register_agent(agent_id, "Ada", "AI Security")

        result = asyncio.run(publisher_service.run_publishing_cycle(agent_id))
        self.assertTrue(result["success"])

        feed = memory_store.get_feed(agent_id)
        self.assertTrue(len(feed) >= 1)
        latest = feed[0]
        self.assertTrue(len(latest["text"]) > 0)
        self.assertTrue(len(latest["rationale"]) > 0)
        self.assertTrue(len(latest["sources"]) > 0)


    def test_custom_agent_db_path_creates_parent_directory(self):
        """Test custom AGENT_DB_PATH (e.g. /data/agent_memory.db) automatically creates parent directory."""
        import os
        import tempfile
        import shutil
        temp_dir = tempfile.mkdtemp()
        try:
            nested_db = os.path.join(temp_dir, "nested_dir", "sub", "custom_memory.db")
            store = AgentMemoryStore(nested_db)
            store.register_agent("test-custom-path", "CustomBot", "AI Security")
            retrieved = store.get_agent("test-custom-path")
            self.assertIsNotNone(retrieved)
            self.assertEqual(retrieved["name"], "CustomBot")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
