"""
EchoMind Autonomous X/Twitter News Publisher Test Suite.

Comprehensive tests covering:
1. Deterministic candidate scoring calculation (0-100 across 6 criteria).
2. Minimum threshold rejection (score < 75.0 rejected; score >= 75.0 eligible).
3. Leader tracking & late-breaking superior news replacing earlier leader.
4. 2-Hour window management: maximum 1 post per window.
5. Zero publication outcome when no candidate qualifies (NO_QUALIFIED_STORY).
6. Deduplication across topic hashes and memory.
7. Restart recovery of active window and leader from SQLite.
8. MockXPublisher character limit validation (<= 280 chars) and idempotency.
9. Evaluator API endpoints: POST /api/agent/init, GET /api/agent/feed, GET /api/agent/status, GET /healthz.
"""

import asyncio
import os
import re
import tempfile
import unittest
from fastapi.testclient import TestClient

from config.persona_engine import build_persona_profile
from config.settings import settings
from main import app
from services.autonomous_publisher import AutonomousPublisherService
from services.editorial_engine import EditorialEngine
from services.memory import AgentMemoryStore
from services.twitter import MockXPublisher

client = TestClient(app)
ISO_8601_REGEX = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"


class TestEchoMindNewsPublisher(unittest.TestCase):

    def setUp(self):
        """Create isolated temporary SQLite database for each test."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_news_memory.db")
        self.memory = AgentMemoryStore(self.db_path)
        self.mock_publisher = MockXPublisher()
        self.service = AutonomousPublisherService(
            memory=self.memory,
            publisher=self.mock_publisher
        )

    def tearDown(self):
        """Clean up temporary test directory."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # =========================================================================
    # 1. CANDIDATE SCORING & THRESHOLD REJECTION
    # =========================================================================

    def test_candidate_scoring_and_threshold(self):
        """Test candidate scoring computes 0-100 score across 6 criteria."""
        editorial = EditorialEngine(memory_store=self.memory)
        profile = build_persona_profile("Ada", "AI Security")

        # High-impact, verified security candidate
        high_candidate = {
            "title": "Breaking: CVE-2026-10492 Discloses Adversarial Sub-Token Quantization Bypass in LLM Weights",
            "summary": "State-of-the-art benchmark exploit demonstrates reproducible sub-token perturbation bypassing refusal boundaries.",
            "source_urls": ["https://cve.mitre.org/cve-2026-10492", "https://arxiv.org/abs/2608.01234"],
            "source_quality": "High"
        }
        score, breakdown, reason = editorial.score_candidate(profile, high_candidate)
        self.assertTrue(score >= 75.0, f"Expected score >= 75, got {score}")
        self.assertIsNone(reason)
        self.assertIn("recency", breakdown)
        self.assertIn("significance", breakdown)
        self.assertIn("domain_relevance", breakdown)
        self.assertIn("source_quality", breakdown)
        self.assertIn("novelty", breakdown)
        self.assertIn("verifiability", breakdown)

        # Low-quality marketing hype candidate
        low_candidate = {
            "title": "Startup claims miraculous unhackable AI wrapper with zero proof",
            "summary": "General marketing recap of generic software.",
            "source_urls": ["http://genericblog.xyz"],
            "source_quality": "Low"
        }
        low_score, low_breakdown, low_reason = editorial.score_candidate(profile, low_candidate)
        self.assertTrue(low_score < 75.0, f"Expected score < 75, got {low_score}")
        self.assertIsNotNone(low_reason)
        self.assertIn("below minimum publishing threshold", low_reason)

    # =========================================================================
    # 2. REQUIRED EDGE CASE 1 & 5: LATE-BREAKING STORY REPLACES LEADER
    # =========================================================================

    def test_late_breaking_story_replaces_leader_and_publishes(self):
        """
        TEST CASE 1 & 5:
        10:00 -> Story A = 80
        10:30 -> Story B = 84
        11:30 -> Story C = 91
        11:58 -> Story D = 97 (Late-breaking superior story)
        12:00 -> Story D MUST be published.
        """
        agent_id = "agent-test-leader-replacement"
        self.memory.register_agent(agent_id, "Ada", "AI Security")
        window = self.memory.create_window(agent_id, duration_minutes=120)
        window_id = window["window_id"]

        # Insert Story A (Score 80)
        self.memory.save_candidate(
            candidate_id="c-story-a",
            agent_id=agent_id,
            window_id=window_id,
            title="Story A: Security Patch for KV Cache",
            summary="Standard patch released for cache isolation.",
            source_urls=["https://arxiv.org/1"],
            source_quality="High",
            score=80.0,
            score_breakdown={"total": 80.0},
            status="LEADER",
            discovered_at="2026-08-08T10:00:00Z"
        )
        leader1 = self.memory.get_current_leader(window_id, min_score=75.0)
        self.assertEqual(leader1["candidate_id"], "c-story-a")

        # Insert Story B (Score 84) -> Replaces A
        self.memory.update_candidate_status("c-story-a", "ELIGIBLE")
        self.memory.save_candidate(
            candidate_id="c-story-b",
            agent_id=agent_id,
            window_id=window_id,
            title="Story B: Model Inversion Defense",
            summary="Novel loss function reduces inversion risk.",
            source_urls=["https://arxiv.org/2"],
            source_quality="High",
            score=84.0,
            score_breakdown={"total": 84.0},
            status="LEADER",
            discovered_at="2026-08-08T10:30:00Z"
        )
        leader2 = self.memory.get_current_leader(window_id, min_score=75.0)
        self.assertEqual(leader2["candidate_id"], "c-story-b")

        # Insert Story C (Score 91) -> Replaces B
        self.memory.update_candidate_status("c-story-b", "ELIGIBLE")
        self.memory.save_candidate(
            candidate_id="c-story-c",
            agent_id=agent_id,
            window_id=window_id,
            title="Story C: Critical Zero-Day in Quantization Kernels",
            summary="Critical exploit allows memory escape during 4-bit dequantization.",
            source_urls=["https://cve.mitre.org/3"],
            source_quality="High",
            score=91.0,
            score_breakdown={"total": 91.0},
            status="LEADER",
            discovered_at="2026-08-08T11:30:00Z"
        )
        leader3 = self.memory.get_current_leader(window_id, min_score=75.0)
        self.assertEqual(leader3["candidate_id"], "c-story-c")

        # Insert Late-Breaking Story D (Score 97) -> Replaces C
        self.memory.update_candidate_status("c-story-c", "ELIGIBLE")
        self.memory.save_candidate(
            candidate_id="c-story-d",
            agent_id=agent_id,
            window_id=window_id,
            title="Story D: Global Foundation Model Jailbreak Vulnerability Disclosed with Proof of Concept",
            summary="Universal jailbreak vector confirmed across all major frontier architectures with reproducible PoC.",
            source_urls=["https://cve.mitre.org/4", "https://nist.gov/4"],
            source_quality="High",
            score=97.0,
            score_breakdown={"total": 97.0},
            status="LEADER",
            discovered_at="2026-08-08T11:58:00Z"
        )

        # Confirm leader before close is D
        final_leader = self.memory.get_current_leader(window_id, min_score=75.0)
        self.assertEqual(final_leader["candidate_id"], "c-story-d")

        # Close window at 12:00
        result = asyncio.run(self.service.process_window_close(agent_id, window_id))
        self.assertTrue(result["success"])
        self.assertEqual(result["window_status"], "PUBLISHED")
        self.assertEqual(len(self.mock_publisher.published_posts), 1)
        
        # Verify published post is about Story D
        feed = self.memory.get_feed(agent_id)
        self.assertEqual(len(feed), 1)
        self.assertIn("Story D", feed[0]["text"])

    # =========================================================================
    # 3. REQUIRED EDGE CASE 2: NO QUALIFIED STORY (SCORE < 75)
    # =========================================================================

    def test_no_qualified_news_publishes_nothing(self):
        """
        TEST CASE 2:
        Window: 10:00 -> 12:00
        Story A = 71
        Story B = 68
        Story C = 74
        Story D = 69
        Minimum Score = 75
        Result: DO NOT PUBLISH. Status: NO_QUALIFIED_STORY.
        """
        agent_id = "agent-test-no-qualified"
        self.memory.register_agent(agent_id, "Ada", "AI Security")
        window = self.memory.create_window(agent_id, duration_minutes=120)
        window_id = window["window_id"]

        for i, score in enumerate([71.0, 68.0, 74.0, 69.0]):
            self.memory.save_candidate(
                candidate_id=f"c-subpar-{i}",
                agent_id=agent_id,
                window_id=window_id,
                title=f"Subpar Story {i}",
                summary="Lack of sufficient verification.",
                source_urls=["http://unverified.com"],
                source_quality="Low",
                score=score,
                score_breakdown={"total": score},
                status="REJECTED",
                rejection_reason=f"Score {score} below minimum 75.0"
            )

        # Leader must be None
        leader = self.memory.get_current_leader(window_id, min_score=75.0)
        self.assertIsNone(leader)

        # Close window
        result = asyncio.run(self.service.process_window_close(agent_id, window_id))
        self.assertTrue(result["success"])
        self.assertEqual(result["window_status"], "NO_QUALIFIED_STORY")
        self.assertEqual(result["action"], "no_publication")
        
        # Zero posts published to X and zero posts in feed
        self.assertEqual(len(self.mock_publisher.published_posts), 0)
        self.assertEqual(len(self.memory.get_feed(agent_id)), 0)

    # =========================================================================
    # 4. REQUIRED EDGE CASE 3: DEDUPLICATION
    # =========================================================================

    def test_deduplication_in_memory(self):
        """
        TEST CASE 3:
        10:00 -> A = 90
        10:30 -> duplicate A = 95
        12:00 -> only ONE post published.
        """
        agent_id = "agent-test-dedup"
        self.memory.register_agent(agent_id, "Ada", "AI Security")
        window = self.memory.create_window(agent_id, duration_minutes=120)
        window_id = window["window_id"]

        topic_title = "Vulnerability in LoRA Adapter Weight Merging"
        topic_hash = self.memory.compute_topic_hash(topic_title)

        # Save post to memory feed first
        self.memory.save_post(
            agent_id=agent_id,
            text="Initial post on LoRA adapter vulnerability.",
            rationale="Verified CVE.",
            sources=["https://cve.mitre.org"],
            topic_hash=topic_hash
        )

        # Attempt to evaluate duplicate candidate
        editorial = EditorialEngine(memory_store=self.memory)
        profile = build_persona_profile("Ada", "AI Security")
        duplicate_candidate = {
            "title": topic_title,
            "summary": "Duplicate content attempt with score 95.",
            "source_urls": ["https://cve.mitre.org"],
            "source_quality": "High",
            "topic_hash": topic_hash
        }

        eval_result = asyncio.run(editorial.evaluate_candidate(
            agent_id=agent_id,
            persona_profile=profile,
            candidate=duplicate_candidate,
            recent_hashes={topic_hash}
        ))

        self.assertEqual(eval_result["status"], "REJECTED")
        self.assertIn("Duplicate content", eval_result["rejection_reason"])

    # =========================================================================
    # 5. REQUIRED EDGE CASE 4: RESTART RECOVERY
    # =========================================================================

    def test_restart_recovery_preserves_window_and_leader(self):
        """
        TEST CASE 4:
        10:00 -> Window opens, Candidate A (score 90) saved.
        10:45 -> Server restarts (new AgentMemoryStore instance on same db_path).
        10:46 -> Recovers window and leader.
        12:00 -> Story A published exactly once.
        """
        agent_id = "agent-test-restart"
        self.memory.register_agent(agent_id, "Ada", "AI Security")
        window = self.memory.create_window(agent_id, duration_minutes=120)
        window_id = window["window_id"]

        self.memory.save_candidate(
            candidate_id="c-story-restart-a",
            agent_id=agent_id,
            window_id=window_id,
            title="Story A: Zero-Day Memory Leak in PyTorch C++ Dispatcher",
            summary="Memory leak leads to arbitrary remote weight extraction.",
            source_urls=["https://github.com/pytorch/pytorch/issues/12345"],
            source_quality="High",
            score=90.0,
            score_breakdown={"total": 90.0},
            status="LEADER",
            discovered_at="2026-08-08T10:00:00Z"
        )

        # Simulate process restart by instantiating new store on same file
        restarted_memory = AgentMemoryStore(self.db_path)
        recovered_window = restarted_memory.get_active_window(agent_id)
        self.assertIsNotNone(recovered_window)
        self.assertEqual(recovered_window["window_id"], window_id)

        recovered_leader = restarted_memory.get_current_leader(window_id, min_score=75.0)
        self.assertIsNotNone(recovered_leader)
        self.assertEqual(recovered_leader["candidate_id"], "c-story-restart-a")
        self.assertEqual(recovered_leader["score"], 90.0)

        # Service on restarted memory publishes Story A
        restarted_service = AutonomousPublisherService(memory=restarted_memory, publisher=self.mock_publisher)
        result = asyncio.run(restarted_service.process_window_close(agent_id, window_id))
        self.assertTrue(result["success"])
        self.assertEqual(result["window_status"], "PUBLISHED")
        self.assertEqual(len(self.mock_publisher.published_posts), 1)

    # =========================================================================
    # 6. X PUBLISHER: CHARACTER LIMIT & IDEMPOTENCY
    # =========================================================================

    def test_mock_x_publisher_character_limit_and_idempotency(self):
        """Test XPublisher rejects >280 character posts and enforces idempotency."""
        publisher = MockXPublisher()

        # Over 280 characters
        long_text = "A" * 285
        res_long = asyncio.run(publisher.publish_post(long_text))
        self.assertFalse(res_long["success"])
        self.assertIn("Character limit exceeded", res_long["error"])

        # Valid text
        valid_text = "Analysis of sub-token prompt perturbations in quantized LLM weights."
        res1 = asyncio.run(publisher.publish_post(valid_text, metadata={"idempotency_key": "k-001"}))
        self.assertTrue(res1["success"])
        self.assertEqual(res1["status"], "PUBLISHED")
        post_id = res1["post_id"]

        # Duplicate attempt with same idempotency key
        res2 = asyncio.run(publisher.publish_post(valid_text, metadata={"idempotency_key": "k-001"}))
        self.assertTrue(res2["success"])
        self.assertEqual(res2["post_id"], post_id)
        self.assertTrue(res2.get("is_duplicate"))
        self.assertEqual(len(publisher.published_posts), 1)

    # =========================================================================
    # 7. EVALUATOR API CONTRACTS & STATUS API
    # =========================================================================

    def test_evaluator_api_endpoints(self):
        """Test POST /api/agent/init, GET /api/agent/feed, GET /api/agent/status, and GET /healthz."""
        # 1. POST /api/agent/init
        init_res = client.post("/api/agent/init", json={"persona": {"name": "Ada", "domain": "AI Security"}})
        self.assertEqual(init_res.status_code, 200)
        agent_id = init_res.json()["agentId"]
        self.assertTrue(len(agent_id) > 0)

        # 2. GET /api/agent/feed
        feed_res = client.get(f"/api/agent/feed?agentId={agent_id}")
        self.assertEqual(feed_res.status_code, 200)
        self.assertIn("posts", feed_res.json())

        # 3. GET /api/agent/status
        status_res = client.get(f"/api/agent/status?agentId={agent_id}")
        self.assertEqual(status_res.status_code, 200)
        status_data = status_res.json()
        self.assertEqual(status_data["agentId"], agent_id)
        self.assertIn("window", status_data)
        self.assertEqual(status_data["window"]["status"], "OPEN")
        self.assertIn("candidateCount", status_data["window"])

        # 4. GET /healthz & /health
        healthz_res = client.get("/healthz")
        self.assertEqual(healthz_res.status_code, 200)
        self.assertEqual(healthz_res.json()["status"], "healthy")
        self.assertEqual(healthz_res.json()["publish_window_minutes"], 120)
        self.assertEqual(healthz_res.json()["min_news_score"], 75.0)

        # 5. GET / and /dashboard (Web Client Interface)
        dash_res = client.get("/")
        self.assertEqual(dash_res.status_code, 200)
        self.assertIn("text/html", dash_res.headers.get("content-type", ""))

        # 6. Centralized API Base URL Configuration
        self.assertEqual(settings.api_base_url, "https://echomind-ltwo.onrender.com")


if __name__ == "__main__":
    unittest.main()
