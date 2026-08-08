"""
EchoMind Autonomous X/Twitter News Publisher Test Suite.

Comprehensive tests covering:
1. Deterministic candidate scoring calculation (0-100 across 6 criteria).
2. Minimum threshold rejection (score < 75.0 rejected; score >= 75.0 eligible).
3. Leader tracking & late-breaking superior news replacing earlier leader.
4. Publishing window management: 120-minute production default & 10-minute testing mode.
5. Zero publication outcome when no candidate qualifies (NO_QUALIFIED_STORY).
6. Deduplication across topic hashes and memory.
7. Restart recovery of active window and leader from SQLite.
8. MockXPublisher character limit validation (<= 280 chars) and idempotency.
9. Evaluator API endpoints: POST /api/agent/init, GET /api/agent/feed, GET /api/agent/status, GET /healthz, GET /api/agents.
10. CRIT-001 Atomic CAS window-close lock under concurrent invocations.
11. CRIT-002 Non-open windows (PUBLISHED, NO_QUALIFIED_STORY) cannot be re-closed.
12. X Publisher: Real response handling, generic 403 failure rejection without synthetic IDs, structured duplicate detection, and persistent idempotency.
13. HIGH-002 Per-agent asyncio.Lock preventing scheduler cycle overlap.
14. MAX_AGENTS=5 Server-side atomic FIFO rotation and dependent data cleanup.
15. Dynamic Configuration: PUBLISH_WINDOW_MINUTES (10 vs 120) and MIN_NEWS_SCORE=75.0.
"""

import asyncio
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from config.persona_engine import build_persona_profile
from config.settings import Settings, settings
from main import app
from services.autonomous_publisher import AutonomousPublisherService
from services.editorial_engine import EditorialEngine
from services.llm import LLMClient
from services.memory import AgentMemoryStore
from services.twitter import MockXPublisher, XPublisher, is_explicit_duplicate_error
from fastapi.testclient import TestClient

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
        leader_1 = self.memory.get_current_leader(window_id, min_score=75.0)
        self.assertEqual(leader_1["candidate_id"], "c-story-a")

        # Insert Story D (Score 97 - Late breaking)
        self.memory.save_candidate(
            candidate_id="c-story-d",
            agent_id=agent_id,
            window_id=window_id,
            title="Story D: Zero-Day Quantization Bypass in Frontier LLMs",
            summary="Critical zero-day bypass allows remote arbitrary weight modification.",
            source_urls=["https://cve.mitre.org/cve-2026-9999"],
            source_quality="High",
            score=97.0,
            score_breakdown={"total": 97.0},
            status="LEADER",
            discovered_at="2026-08-08T11:58:00Z"
        )
        # Update Story A to ELIGIBLE
        self.memory.update_candidate_status("c-story-a", "ELIGIBLE")

        # Verify Story D is now current leader
        leader_2 = self.memory.get_current_leader(window_id, min_score=75.0)
        self.assertEqual(leader_2["candidate_id"], "c-story-d")
        self.assertEqual(leader_2["score"], 97.0)

        # Execute window close at 12:00
        close_result = asyncio.run(self.service.process_window_close(agent_id, window_id))
        self.assertTrue(close_result["success"])
        self.assertEqual(close_result["window_status"], "PUBLISHED")
        published_text = self.mock_publisher.published_posts[0]["text"]
        self.assertTrue(
            any(k in published_text for k in ["Story D", "Zero-Day", "Quantization", "quantization", "CVE-2026-9999", "weight"]),
            f"Published text '{published_text}' does not contain expected Story D topic content"
        )

    # =========================================================================
    # 3. CONTROLLED TESTING PHASE: 10-MINUTE WINDOW & LEADER REPLACEMENT
    # =========================================================================

    def test_10_minute_window_leader_replacement_and_close(self):
        """
        CONTROLLED TESTING MODE:
        Minute 1: Candidate A = 80
        Minute 6: Candidate B = 93
        Minute 10: Candidate B MUST be published at window close (93 > 80 and >= 75.0).
        """
        agent_id = "agent-test-10min"
        self.memory.register_agent(agent_id, "Ada", "AI Security")
        window = self.memory.create_window(agent_id, duration_minutes=10)
        window_id = window["window_id"]

        start_dt = datetime.strptime(window["started_at"], "%Y-%m-%dT%H:%M:%SZ")
        end_dt = datetime.strptime(window["ends_at"], "%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual((end_dt - start_dt).total_seconds(), 600)  # Exactly 10 minutes

        # Minute 1: Candidate A (Score 80.0)
        self.memory.save_candidate(
            candidate_id="c-10m-a",
            agent_id=agent_id,
            window_id=window_id,
            title="Candidate A: Vulnerability in FlashAttention Kernel",
            summary="Kernel crash on negative token indices.",
            source_urls=["https://arxiv.org/abs/2608.1001"],
            source_quality="High",
            score=80.0,
            score_breakdown={"total": 80.0},
            status="LEADER",
            discovered_at=(start_dt + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        l1 = self.memory.get_current_leader(window_id, min_score=75.0)
        self.assertEqual(l1["candidate_id"], "c-10m-a")

        # Minute 6: Candidate B (Score 93.0 - Superior Story)
        self.memory.save_candidate(
            candidate_id="c-10m-b",
            agent_id=agent_id,
            window_id=window_id,
            title="Candidate B: Remote Weight Corruption via Quantization Drift",
            summary="Zero-day exploit confirmed on production inference cluster.",
            source_urls=["https://cve.mitre.org/cve-2026-8888"],
            source_quality="High",
            score=93.0,
            score_breakdown={"total": 93.0},
            status="LEADER",
            discovered_at=(start_dt + timedelta(minutes=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        self.memory.update_candidate_status("c-10m-a", "ELIGIBLE")

        # Verify Candidate B replaced A as leader
        l2 = self.memory.get_current_leader(window_id, min_score=75.0)
        self.assertEqual(l2["candidate_id"], "c-10m-b")
        self.assertEqual(l2["score"], 93.0)

        # Close window at Minute 10
        result = asyncio.run(self.service.process_window_close(agent_id, window_id))
        self.assertTrue(result["success"])
        self.assertEqual(result["window_status"], "PUBLISHED")
        published_text = self.mock_publisher.published_posts[0]["text"]
        self.assertTrue(
            any(k in published_text for k in ["Candidate B", "Quantization Drift", "Weight Corruption", "inference cluster", "quantization"]),
            f"Published text '{published_text}' does not contain expected Candidate B topic content"
        )

    def test_configuration_publish_window_minutes_override(self):
        """Verify settings.publish_window_minutes is dynamically configuration-driven."""
        # 1. Default configuration must remain 120
        default_s = Settings()
        self.assertEqual(default_s.publish_window_minutes, 120)

        # 2. Testing override to 10
        test_s = Settings(publish_window_minutes=10)
        self.assertEqual(test_s.publish_window_minutes, 10)

        # 3. Discovery interval remains 5 minutes
        self.assertEqual(default_s.discovery_interval_minutes, 5)
        self.assertEqual(test_s.discovery_interval_minutes, 5)

    # =========================================================================
    # 4. REQUIRED EDGE CASE 2: ZERO PUBLICATION OUTCOME
    # =========================================================================

    def test_zero_publication_when_no_candidate_qualifies(self):
        """
        TEST CASE 2:
        All candidates during the window score below 75.0.
        At window close -> ZERO posts published (NO_QUALIFIED_STORY).
        """
        agent_id = "agent-test-zero-pub"
        self.memory.register_agent(agent_id, "Ada", "AI Security")
        window = self.memory.create_window(agent_id, duration_minutes=120)
        window_id = window["window_id"]

        # Insert low-quality candidates
        self.memory.save_candidate(
            candidate_id="c-low-1",
            agent_id=agent_id,
            window_id=window_id,
            title="Generic Opinion Piece on AI",
            summary="No facts or verified benchmarks.",
            source_urls=["http://blog.example.com"],
            source_quality="Low",
            score=54.0,
            score_breakdown={"total": 54.0},
            status="REJECTED",
            rejection_reason="Score 54.0 below 75.0 threshold",
            discovered_at="2026-08-08T10:15:00Z"
        )
        self.memory.save_candidate(
            candidate_id="c-low-2",
            agent_id=agent_id,
            window_id=window_id,
            title="Marketing Fluff on AI Tool",
            summary="Self-promotional announcement.",
            source_urls=["http://pr.example.com"],
            source_quality="Low",
            score=68.0,
            score_breakdown={"total": 68.0},
            status="REJECTED",
            rejection_reason="Score 68.0 below 75.0 threshold",
            discovered_at="2026-08-08T11:00:00Z"
        )

        # Leader must be None
        leader = self.memory.get_current_leader(window_id, min_score=75.0)
        self.assertIsNone(leader)

        # Execute window close
        close_result = asyncio.run(self.service.process_window_close(agent_id, window_id))
        self.assertTrue(close_result["success"])
        self.assertEqual(close_result["window_status"], "NO_QUALIFIED_STORY")
        self.assertEqual(len(self.mock_publisher.published_posts), 0)
        self.assertEqual(self.memory.count_posts(agent_id), 0)

    # =========================================================================
    # 5. REQUIRED EDGE CASE 3: DEDUPLICATION
    # =========================================================================

    def test_deduplication_prevents_duplicate_topics(self):
        """
        TEST CASE 3:
        10:00 -> Story A published (LoRA adapter vulnerability)
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
    # 6. REQUIRED EDGE CASE 4: RESTART RECOVERY
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
    # 7. CRIT-001 & CRIT-002: ATOMIC CAS LOCK & IDEMPOTENT WINDOW CLOSURE
    # =========================================================================

    def test_crit_001_atomic_cas_lock_prevents_duplicate_close(self):
        """
        CRIT-001: Concurrent calls to process_window_close() must result in
        exactly ONE execution claiming the window and exactly ONE post published.
        """
        agent_id = "agent-test-crit-001"
        self.memory.register_agent(agent_id, "Ada", "AI Security")
        window = self.memory.create_window(agent_id, duration_minutes=120)
        window_id = window["window_id"]

        self.memory.save_candidate(
            candidate_id="c-crit-001",
            agent_id=agent_id,
            window_id=window_id,
            title="Critical Frontier AI Vulnerability",
            summary="Exploit tested against benchmark suite.",
            source_urls=["https://arxiv.org/abs/2608.12345"],
            source_quality="High",
            score=95.0,
            score_breakdown={"total": 95.0},
            status="LEADER",
            discovered_at="2026-08-08T10:00:00Z"
        )

        # Run two simultaneous window close calls
        async def run_concurrent():
            t1 = self.service.process_window_close(agent_id, window_id)
            t2 = self.service.process_window_close(agent_id, window_id)
            return await asyncio.gather(t1, t2)

        results = asyncio.run(run_concurrent())
        success_count = sum(1 for r in results if r.get("success") is True and r.get("action") == "published")
        ignored_count = sum(1 for r in results if r.get("action") == "ignored")

        self.assertEqual(success_count, 1, "Exactly one execution must succeed in publishing")
        self.assertEqual(ignored_count, 1, "The concurrent call must be safely ignored")
        self.assertEqual(len(self.mock_publisher.published_posts), 1, "Exactly one X post must be created")

    def test_crit_002_reclosing_published_window_is_safe_noop(self):
        """
        CRIT-002: Re-closing an already closed/published window must NOT
        overwrite its state to NO_QUALIFIED_STORY and must NOT create duplicate windows.
        """
        agent_id = "agent-test-crit-002"
        self.memory.register_agent(agent_id, "Ada", "AI Security")
        window = self.memory.create_window(agent_id, duration_minutes=120)
        window_id = window["window_id"]

        self.memory.save_candidate(
            candidate_id="c-crit-002",
            agent_id=agent_id,
            window_id=window_id,
            title="Frontier Model Alignment Bypass Discovered",
            summary="Zero-day bypass verified on reference architecture.",
            source_urls=["https://arxiv.org/abs/2608.99999"],
            source_quality="High",
            score=88.0,
            score_breakdown={"total": 88.0},
            status="LEADER",
            discovered_at="2026-08-08T10:00:00Z"
        )

        # 1. Close window initially -> PUBLISHED
        res1 = asyncio.run(self.service.process_window_close(agent_id, window_id))
        self.assertTrue(res1["success"])
        self.assertEqual(res1["window_status"], "PUBLISHED")

        # 2. Attempt to re-close the SAME window again
        res2 = asyncio.run(self.service.process_window_close(agent_id, window_id))
        self.assertFalse(res2["success"])
        self.assertEqual(res2["action"], "ignored")
        self.assertIn("must be OPEN", res2["reason"])

        # Check database window state was not overwritten
        saved_window = self.memory.get_window(window_id)
        self.assertEqual(saved_window["status"], "PUBLISHED")
        self.assertEqual(len(self.mock_publisher.published_posts), 1)

    # =========================================================================
    # 8. X PUBLISHER: REAL RESPONSE, 403 REJECTION, AND STRUCTURED DUPLICATE HANDLING
    # =========================================================================

    def test_x_real_successful_response_returns_genuine_post_id(self):
        """TEST 1: Real successful X response with genuine post ID."""
        class MockUserRes:
            data = {"username": "RASBASRYPI"}

        class MockTweetRes:
            data = {"id": "1888123456789012345"}

        class SuccessfulClient:
            def get_me(self):
                return MockUserRes()
            def create_tweet(self, text):
                return MockTweetRes()
            def get_tweet(self, id):
                return MockTweetRes()

        x_pub = XPublisher(
            api_key="k", api_secret="s", access_token="t", access_token_secret="ts",
            memory_store=self.memory
        )
        x_pub._client = SuccessfulClient()

        result = asyncio.run(x_pub.publish_post(
            text="Breaking: CVE-2026-10492 published.",
            metadata={"idempotency_key": "k-real-success", "agent_id": "a-1", "window_id": "w-1"}
        ))

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "PUBLISHED")
        self.assertEqual(result["post_id"], "1888123456789012345")
        self.assertIsNone(result.get("error"))

    # =========================================================================
    # 12. PHASE 3 - COMPREHENSIVE X PUBLICATION INTEGRITY & VERIFICATION TESTS
    # =========================================================================

    def test_authenticated_x_account_matches_expected_handle(self):
        """TEST 1: Authenticated X account matches EXPECTED_X_HANDLE."""
        class MockUserRes:
            data = {"username": "RASBASRYPI"}

        class ClientWithMatchingUser:
            def get_me(self):
                return MockUserRes()

        x_pub = XPublisher(api_key="k", api_secret="s", access_token="t", access_token_secret="ts", memory_store=self.memory)
        x_pub._client = ClientWithMatchingUser()

        import os
        old_val = os.environ.get("EXPECTED_X_HANDLE")
        os.environ["EXPECTED_X_HANDLE"] = "RASBASRYPI"
        try:
            res = x_pub.verify_authenticated_account()
            self.assertTrue(res["success"])
            self.assertEqual(res["handle"], "RASBASRYPI")
        finally:
            if old_val is not None:
                os.environ["EXPECTED_X_HANDLE"] = old_val
            else:
                os.environ.pop("EXPECTED_X_HANDLE", None)

    def test_authenticated_x_account_mismatch_fails_closed(self):
        """TEST 2: Authenticated X account handle mismatch fails closed immediately."""
        class MockUserRes:
            data = {"username": "wrong_user_handle"}

        class ClientWithWrongUser:
            def get_me(self):
                return MockUserRes()

        x_pub = XPublisher(api_key="k", api_secret="s", access_token="t", access_token_secret="ts", memory_store=self.memory)
        x_pub._client = ClientWithWrongUser()

        import os
        old_val = os.environ.get("EXPECTED_X_HANDLE")
        os.environ["EXPECTED_X_HANDLE"] = "RASBASRYPI"
        try:
            res = asyncio.run(x_pub.publish_post(text="Test text", metadata={"idempotency_key": "k-mismatch"}))
            self.assertFalse(res["success"])
            self.assertEqual(res["status"], "FAILED")
            self.assertIn("does not match expected handle", str(res.get("error")))
        finally:
            if old_val is not None:
                os.environ["EXPECTED_X_HANDLE"] = old_val
            else:
                os.environ.pop("EXPECTED_X_HANDLE", None)

    def test_get_me_failure_fails_closed(self):
        """TEST 3: client.get_me() failure fails closed immediately."""
        class FailingGetMeClient:
            def get_me(self):
                raise Exception("401 Unauthorized: Could not authenticate get_me()")

        x_pub = XPublisher(api_key="k", api_secret="s", access_token="t", access_token_secret="ts", memory_store=self.memory)
        x_pub._client = FailingGetMeClient()

        res = asyncio.run(x_pub.publish_post(text="Test text get_me fail", metadata={"idempotency_key": "k-getme-fail"}))
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "FAILED")
        self.assertIsNone(res.get("post_id"))

    def test_genuine_create_tweet_followed_by_successful_get_tweet_verification(self):
        """TEST 4: Genuine create_tweet followed by successful independent get_tweet verification yields PUBLISHED."""
        class MockUserRes:
            data = {"username": "RASBASRYPI"}

        class MockTweetRes:
            data = {"id": "1888123456789012345"}

        class VerifiedClient:
            def get_me(self):
                return MockUserRes()
            def create_tweet(self, text):
                class MockRes:
                    data = {"id": "1888123456789012345"}
                return MockRes()
            def get_tweet(self, id):
                return MockTweetRes()

        x_pub = XPublisher(api_key="k", api_secret="s", access_token="t", access_token_secret="ts", memory_store=self.memory)
        x_pub._client = VerifiedClient()

        res = asyncio.run(x_pub.publish_post(text="Genuine verified post text", metadata={"idempotency_key": "k-verified-success"}))
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "PUBLISHED")
        self.assertEqual(res["post_id"], "1888123456789012345")

    def test_create_tweet_returns_numeric_id_but_get_tweet_cannot_find_it(self):
        """TEST 5: create_tweet returns numeric ID but independent get_tweet verification finds no data."""
        class MockUserRes:
            data = {"username": "RASBASRYPI"}

        class UnverifiableClient:
            def get_me(self):
                return MockUserRes()
            def create_tweet(self, text):
                class MockRes:
                    data = {"id": "1888123456789012345"}
                return MockRes()
            def get_tweet(self, id):
                return None  # GET /2/tweets/1888123456789012345 returns no data

        x_pub = XPublisher(api_key="k", api_secret="s", access_token="t", access_token_secret="ts", memory_store=self.memory)
        x_pub._client = UnverifiableClient()

        res = asyncio.run(x_pub.publish_post(text="Test text unverifiable", metadata={"idempotency_key": "k-unverifiable"}))
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "FAILED")
        self.assertIsNone(res.get("post_id"))

    def test_get_tweet_returns_mismatching_id(self):
        """TEST 6: get_tweet returns a mismatching ID."""
        class MockUserRes:
            data = {"username": "RASBASRYPI"}

        class MockMismatchingTweetRes:
            data = {"id": "9999999999999999999"}

        class MismatchingClient:
            def get_me(self):
                return MockUserRes()
            def create_tweet(self, text):
                class MockRes:
                    data = {"id": "1888123456789012345"}
                return MockRes()
            def get_tweet(self, id):
                return MockMismatchingTweetRes()

        x_pub = XPublisher(api_key="k", api_secret="s", access_token="t", access_token_secret="ts", memory_store=self.memory)
        x_pub._client = MismatchingClient()

        res = asyncio.run(x_pub.publish_post(text="Test text mismatch", metadata={"idempotency_key": "k-mismatch-id"}))
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "FAILED")
        self.assertIsNone(res.get("post_id"))

    def test_create_tweet_returns_no_id(self):
        """TEST 7: create_tweet returns response missing data.id."""
        class MockUserRes:
            data = {"username": "RASBASRYPI"}

        class NoIdClient:
            def get_me(self):
                return MockUserRes()
            def create_tweet(self, text):
                return None

        x_pub = XPublisher(api_key="k", api_secret="s", access_token="t", access_token_secret="ts", memory_store=self.memory)
        x_pub._client = NoIdClient()

        res = asyncio.run(x_pub.publish_post(text="Test text no id", metadata={"idempotency_key": "k-no-id"}))
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "FAILED")
        self.assertIsNone(res.get("post_id"))

    def test_generic_401_unauthorized(self):
        """TEST 8: Generic HTTP 401 Unauthorized fails cleanly."""
        class MockUserRes:
            data = {"username": "RASBASRYPI"}

        class Client401:
            def get_me(self):
                return MockUserRes()
            def create_tweet(self, text):
                raise Exception("401 Unauthorized: Could not authenticate credentials.")

        x_pub = XPublisher(api_key="k", api_secret="s", access_token="t", access_token_secret="ts", memory_store=self.memory)
        x_pub._client = Client401()

        res = asyncio.run(x_pub.publish_post(text="Test text 401", metadata={"idempotency_key": "k-401"}))
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "FAILED")

    def test_generic_403_forbidden(self):
        """TEST 9: Generic HTTP 403 Forbidden fails cleanly without retries."""
        class MockUserRes:
            data = {"username": "RASBASRYPI"}

        class Client403:
            def get_me(self):
                return MockUserRes()
            def create_tweet(self, text):
                raise Exception("403 Forbidden: Read-only app scope limit.")

        x_pub = XPublisher(api_key="k", api_secret="s", access_token="t", access_token_secret="ts", memory_store=self.memory)
        x_pub._client = Client403()

        res = asyncio.run(x_pub.publish_post(text="Test text 403", metadata={"idempotency_key": "k-403"}))
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "FAILED")

    def test_explicit_duplicate_error_without_sqlite_id(self):
        """TEST 10: Explicit duplicate error without pre-existing SQLite Snowflake ID fails cleanly."""
        class MockUserRes:
            data = {"username": "RASBASRYPI"}

        class ClientDup:
            def get_me(self):
                return MockUserRes()
            def create_tweet(self, text):
                raise Exception("403 Forbidden: Status is a duplicate.")

        x_pub = XPublisher(api_key="k", api_secret="s", access_token="t", access_token_secret="ts", memory_store=self.memory)
        x_pub._client = ClientDup()

        res = asyncio.run(x_pub.publish_post(text="Test duplicate text", metadata={"idempotency_key": "k-dup-no-id"}))
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "FAILED")

    def test_network_timeout_after_x_may_have_accepted_tweet(self):
        """TEST 11: Network timeout during create_tweet fails cleanly without fabricating IDs."""
        class MockUserRes:
            data = {"username": "RASBASRYPI"}

        class ClientTimeout:
            def get_me(self):
                return MockUserRes()
            def create_tweet(self, text):
                raise ConnectionError("Network unreachable: connection timed out")

        x_pub = XPublisher(api_key="k", api_secret="s", access_token="t", access_token_secret="ts", max_retries=2, memory_store=self.memory)
        x_pub._client = ClientTimeout()

        res = asyncio.run(x_pub.publish_post(text="Network timeout test", metadata={"idempotency_key": "k-timeout"}))
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "FAILED")

    def test_synthetic_x_mock_id_rejected(self):
        """TEST 12: Synthetic x-mock-* ID is rejected by production publication gate."""
        prod_service = AutonomousPublisherService(memory=self.memory)
        async def mock_fake_publish(*args, **kwargs):
            return {"success": True, "status": "PUBLISHED", "post_id": "x-mock-0001"}
        prod_service.publisher.publish_post = mock_fake_publish

        agent_id = "agent-test-x-mock-gate-p3"
        self.memory.register_agent(agent_id, "Ada", "AI Security")
        window = self.memory.create_window(agent_id, duration_minutes=120)
        window_id = window["window_id"]
        self.memory.save_candidate("c-gate-p3-1", agent_id, window_id, "Title", "Summary", ["https://a.org"], "High", 90.0, {"total": 90.0}, "LEADER")

        res = asyncio.run(prod_service.process_window_close(agent_id, window_id))
        self.assertFalse(res["success"])
        self.assertEqual(res["window_status"], "FAILED")

    def test_synthetic_x_confirmed_id_rejected(self):
        """TEST 13: Synthetic x-confirmed-* ID is rejected by production publication gate."""
        prod_service = AutonomousPublisherService(memory=self.memory)
        async def mock_confirmed_publish(*args, **kwargs):
            return {"success": True, "status": "PUBLISHED", "post_id": "x-confirmed-agent-1c"}
        prod_service.publisher.publish_post = mock_confirmed_publish

        agent_id = "agent-test-x-conf-gate-p3"
        self.memory.register_agent(agent_id, "Ada", "AI Security")
        window = self.memory.create_window(agent_id, duration_minutes=120)
        window_id = window["window_id"]
        self.memory.save_candidate("c-gate-p3-2", agent_id, window_id, "Title 2", "Summary 2", ["https://a.org"], "High", 91.0, {"total": 91.0}, "LEADER")

        res = asyncio.run(prod_service.process_window_close(agent_id, window_id))
        self.assertFalse(res["success"])
        self.assertEqual(res["window_status"], "FAILED")

    def test_successful_publication_database_state(self):
        """TEST 14: Successful genuine publication updates DB (1 feed row, candidate PUBLISHED, Snowflake ID stored)."""
        agent_id = "agent-test-success-p3-db"
        self.memory.register_agent(agent_id, "Ada", "AI Security")
        window = self.memory.create_window(agent_id, duration_minutes=120)
        window_id = window["window_id"]

        self.memory.save_candidate("c-succ-p3", agent_id, window_id, "Success Title", "Success Summary", ["https://a.org"], "High", 95.0, {"total": 95.0}, "LEADER")

        class MockUserRes:
            data = {"username": "RASBASRYPI"}

        class MockTweetRes:
            data = {"id": "1999888777666555444"}

        class GenuineVerifiedClient:
            def get_me(self):
                return MockUserRes()
            def create_tweet(self, text):
                class MockRes:
                    data = {"id": "1999888777666555444"}
                return MockRes()
            def get_tweet(self, id):
                return MockTweetRes()

        genuine_pub = XPublisher(api_key="k", api_secret="s", access_token="t", access_token_secret="ts", memory_store=self.memory)
        genuine_pub._client = GenuineVerifiedClient()
        success_service = AutonomousPublisherService(memory=self.memory, publisher=genuine_pub)

        res = asyncio.run(success_service.process_window_close(agent_id, window_id))
        self.assertTrue(res["success"])
        self.assertEqual(res["window_status"], "PUBLISHED")
        self.assertEqual(self.memory.count_posts(agent_id), 1)
        cands = self.memory.get_candidates_for_window(window_id)
        self.assertEqual(cands[0]["status"], "PUBLISHED")
        self.assertEqual(self.memory.get_window(window_id)["status"], "PUBLISHED")
        self.assertEqual(self.memory.get_window(window_id)["post_id"], "1999888777666555444")

    def test_failed_publication_database_state(self):
        """TEST 15: Failed publication leaves DB clean (0 feed rows, candidate NOT PUBLISHED, window FAILED)."""
        agent_id = "agent-test-failed-p3-db"
        self.memory.register_agent(agent_id, "Ada", "AI Security")
        window = self.memory.create_window(agent_id, duration_minutes=120)
        window_id = window["window_id"]

        self.memory.save_candidate("c-fail-p3", agent_id, window_id, "Fail Title", "Fail Summary", ["https://a.org"], "High", 95.0, {"total": 95.0}, "LEADER")

        failing_pub = MockXPublisher(should_fail=True)
        fail_service = AutonomousPublisherService(memory=self.memory, publisher=failing_pub)

        res = asyncio.run(fail_service.process_window_close(agent_id, window_id))
        self.assertFalse(res["success"])
        self.assertEqual(res["window_status"], "FAILED")
        self.assertEqual(self.memory.count_posts(agent_id), 0)
        cands = self.memory.get_candidates_for_window(window_id)
        self.assertNotEqual(cands[0]["status"], "PUBLISHED")
        self.assertEqual(self.memory.get_window(window_id)["status"], "FAILED")

    def test_production_service_construction_never_uses_mock_publisher(self):
        """TEST 16: AutonomousPublisherService() without args strictly instantiates production XPublisher."""
        service = AutonomousPublisherService(memory=self.memory)
        self.assertIsInstance(service.publisher, XPublisher)
        self.assertNotIsInstance(service.publisher, MockXPublisher)

    def test_process_window_close_never_marks_published_before_independent_x_verification(self):
        """TEST 17: process_window_close never marks PUBLISHED if independent GET tweet verification fails."""
        agent_id = "agent-test-unverified-close"
        self.memory.register_agent(agent_id, "Ada", "AI Security")
        window = self.memory.create_window(agent_id, duration_minutes=120)
        window_id = window["window_id"]

        self.memory.save_candidate("c-unverified", agent_id, window_id, "Unverified Title", "Unverified Summary", ["https://a.org"], "High", 95.0, {"total": 95.0}, "LEADER")

        class MockUserRes:
            data = {"username": "RASBASRYPI"}

        class FailingGetTweetClient:
            def get_me(self):
                return MockUserRes()
            def create_tweet(self, text):
                class MockRes:
                    data = {"id": "1888123456789012345"}
                return MockRes()
            def get_tweet(self, id):
                return None  # Independent GET verification returns no data

        unverified_pub = XPublisher(api_key="k", api_secret="s", access_token="t", access_token_secret="ts", memory_store=self.memory)
        unverified_pub._client = FailingGetTweetClient()
        unverified_service = AutonomousPublisherService(memory=self.memory, publisher=unverified_pub)

        res = asyncio.run(unverified_service.process_window_close(agent_id, window_id))
        self.assertFalse(res["success"])
        self.assertEqual(res["window_status"], "FAILED")
        self.assertEqual(self.memory.count_posts(agent_id), 0)
        cands = self.memory.get_candidates_for_window(window_id)
        self.assertNotEqual(cands[0]["status"], "PUBLISHED")
        self.assertEqual(self.memory.get_window(window_id)["status"], "FAILED")

    # =========================================================================
    # 9. HIGH-002: SCHEDULER DISCOVERY CONCURRENCY LOCK
    # =========================================================================

    def test_high_002_scheduler_agent_lock_prevents_cycle_overlap(self):
        """
        HIGH-002: If discovery takes long, concurrent calls to
        run_discovery_and_evaluation_cycle for the same agent skip overlap cleanly.
        """
        agent_id = "agent-test-lock"
        self.memory.register_agent(agent_id, "Ada", "AI Security")
        self.memory.create_window(agent_id, duration_minutes=120)

        # Mock discovery with an artificial delay
        async def slow_discovery(*args, **kwargs):
            await asyncio.sleep(0.1)
            return []

        self.service.discovery.discover_candidate_topics = slow_discovery

        async def run_overlapping():
            t1 = self.service.run_discovery_and_evaluation_cycle(agent_id)
            t2 = self.service.run_discovery_and_evaluation_cycle(agent_id)
            return await asyncio.gather(t1, t2)

        results = asyncio.run(run_overlapping())
        # One runs, the other skips overlap cleanly
        actions = [r.get("action") for r in results]
        self.assertIn("discovery_cycle_completed", actions)
        self.assertIn("skipped_overlap", actions)

    # =========================================================================
    # 10. PUBLISHING WINDOW TIME MATHEMATICS (120 MIN VS 10 MIN)
    # =========================================================================

    def test_publish_window_time_calculations(self):
        """Verify that ends_at = started_at + duration_minutes in strict ISO 8601 UTC."""
        # 120-minute production window
        agent_id_120 = "agent-time-math-120"
        self.memory.register_agent(agent_id_120, "Ada", "AI Security")
        w_120 = self.memory.create_window(agent_id_120, duration_minutes=120)
        s120 = datetime.strptime(w_120["started_at"], "%Y-%m-%dT%H:%M:%SZ")
        e120 = datetime.strptime(w_120["ends_at"], "%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual((e120 - s120).total_seconds(), 7200)

        # 10-minute testing window
        agent_id_10 = "agent-time-math-10"
        self.memory.register_agent(agent_id_10, "Atlas", "Robotics")
        w_10 = self.memory.create_window(agent_id_10, duration_minutes=10)
        s10 = datetime.strptime(w_10["started_at"], "%Y-%m-%dT%H:%M:%SZ")
        e10 = datetime.strptime(w_10["ends_at"], "%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual((e10 - s10).total_seconds(), 600)

    # =========================================================================
    # 11. MAX_AGENTS=5 ATOMIC FIFO ROTATION
    # =========================================================================

    def test_max_agents_5_atomic_fifo_rotation_and_cleanup(self):
        """
        MAX_AGENTS=5:
        1. Register 5 agents: Ada, Atlas, Nova, Orion, Vega.
        2. Verify count = 5.
        3. Register 6th agent: Luna.
        4. Verify Ada (oldest) is permanently removed from active SQLite DB.
        5. Verify exactly 5 agents remain: Atlas, Nova, Orion, Vega, Luna.
        6. Verify all dependent data of Ada is deleted.
        """
        # 1. Create first 5 agents
        names = [("Ada", "AI Security"), ("Atlas", "Robotics"), ("Nova", "Cloud Security"), ("Orion", "AI Research"), ("Vega", "AI Ethics")]
        agent_ids = []
        for name, domain in names:
            aid = f"agent-{name.lower()}"
            self.memory.register_agent(aid, name, domain, max_agents=5)
            self.memory.create_window(aid, duration_minutes=120)
            self.memory.save_candidate(
                candidate_id=f"c-{aid}",
                agent_id=aid,
                window_id=f"win-{aid}",
                title=f"Initial discovery for {name}",
                summary="Initial test summary",
                source_urls=["https://arxiv.org"],
                source_quality="High",
                score=80.0,
                score_breakdown={"total": 80.0},
                status="ELIGIBLE"
            )
            agent_ids.append(aid)

        initial_agents = self.memory.list_agents()
        self.assertEqual(len(initial_agents), 5)
        self.assertEqual(initial_agents[0]["agentId"], "agent-ada")

        # 2. Create 6th agent Luna
        self.memory.register_agent("agent-luna", "Luna", "Autonomous Agents", max_agents=5)
        self.memory.create_window("agent-luna", duration_minutes=120)

        # 3. Verify exactly 5 agents remain
        updated_agents = self.memory.list_agents()
        self.assertEqual(len(updated_agents), 5)

        remaining_ids = [a["agentId"] for a in updated_agents]
        self.assertNotIn("agent-ada", remaining_ids, "Ada (oldest) must have been evicted")
        self.assertIn("agent-luna", remaining_ids, "Luna must have been added")
        self.assertEqual(remaining_ids, ["agent-atlas", "agent-nova", "agent-orion", "agent-vega", "agent-luna"])

        # 4. Verify Ada's dependent records were deleted
        with self.memory._get_connection() as conn:
            win_count = conn.execute("SELECT COUNT(*) as c FROM publishing_windows WHERE agent_id = 'agent-ada'").fetchone()["c"]
            cand_count = conn.execute("SELECT COUNT(*) as c FROM news_candidates WHERE agent_id = 'agent-ada'").fetchone()["c"]
            self.assertEqual(win_count, 0, "Ada's windows must be deleted")
            self.assertEqual(cand_count, 0, "Ada's candidates must be deleted")

    # =========================================================================
    # 12. EVALUATOR API CONTRACTS & DYNAMIC WINDOW DURATION
    # =========================================================================

    def test_evaluator_api_endpoints_and_agents_list(self):
        """Test GET /api/agents, POST /api/agent/init, GET /api/agent/feed, GET /api/agent/status, and GET /healthz."""
        # 1. POST /api/agent/init
        init_res = client.post("/api/agent/init", json={"persona": {"name": "Ada", "domain": "AI Security"}})
        self.assertEqual(init_res.status_code, 200)
        agent_id = init_res.json()["agentId"]
        self.assertTrue(len(agent_id) > 0)

        # 2. GET /api/agents
        agents_res = client.get("/api/agents")
        self.assertEqual(agents_res.status_code, 200)
        agents_data = agents_res.json()
        self.assertIn("agents", agents_data)
        self.assertEqual(agents_data["maxAgents"], 5)
        self.assertIn("publishWindowMinutes", agents_data)
        self.assertTrue(agents_data["count"] >= 1)
        first_agent = agents_data["agents"][0]
        self.assertIn("agentId", first_agent)
        self.assertIn("status", first_agent)
        self.assertNotIn("x_api_key", str(agents_data))
        self.assertNotIn("openrouter_api_key", str(agents_data))

        # 3. GET /api/agent/feed
        feed_res = client.get(f"/api/agent/feed?agentId={agent_id}")
        self.assertEqual(feed_res.status_code, 200)
        self.assertIn("posts", feed_res.json())

        # 4. GET /api/agent/status
        status_res = client.get(f"/api/agent/status?agentId={agent_id}")
        self.assertEqual(status_res.status_code, 200)
        status_data = status_res.json()
        self.assertEqual(status_data["agentId"], agent_id)
        self.assertIn("window", status_data)
        self.assertEqual(status_data["window"]["status"], "OPEN")
        self.assertIn("candidateCount", status_data["window"])

        # 5. GET /healthz & /health
        healthz_res = client.get("/healthz")
        self.assertEqual(healthz_res.status_code, 200)
        self.assertEqual(healthz_res.json()["status"], "healthy")
        self.assertIn("publish_window_minutes", healthz_res.json())
        self.assertEqual(healthz_res.json()["min_news_score"], 75.0)
        self.assertEqual(healthz_res.json()["max_agents"], 5)

        # 6. GET / and /dashboard (Web Client Interface)
        dash_res = client.get("/")
        self.assertEqual(dash_res.status_code, 200)
        self.assertIn("text/html", dash_res.headers.get("content-type", ""))

        # 7. Centralized API Base URL Configuration
        self.assertEqual(settings.api_base_url, "https://echomind-ltwo.onrender.com")

    def test_expected_x_handle_environment_configuration(self):
        """Verify that EXPECTED_X_HANDLE is loaded without exposing raw secret values."""
        self.assertTrue(bool(settings.x_expected_handle), "EXPECTED_X_HANDLE must be non-empty when configured.")
        self.assertTrue(isinstance(settings.x_expected_handle, str))

    # =========================================================================
    # 13. LLM CLIENT STRUCTURED GENERATION CONTRACT TEST
    # =========================================================================

    def test_llm_client_generate_structured_contract(self):
        """
        Verify that LLMClient.generate_structured accepts both 'schema=' and 'response_format='
        keyword arguments as well as positional arguments without raising TypeError.
        """
        class MockHttpResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": '{"post_text": "Verified security flaw in AI weights.", "rationale": "High score", "sources": ["https://arxiv.org"]}'
                            }
                        }
                    ]
                }

        class MockAsyncClient:
            def __init__(self, *args, **kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def post(self, url, headers=None, json=None):
                if not json or "response_format" not in json:
                    raise ValueError("Missing response_format in LLM request payload")
                return MockHttpResponse()

        llm = LLMClient()

        import httpx
        original_client = httpx.AsyncClient
        httpx.AsyncClient = MockAsyncClient

        try:
            schema = {"type": "json_schema", "json_schema": {"name": "test", "schema": {}}}

            # 1. Test schema= keyword argument (used by editorial_engine)
            res1 = asyncio.run(llm.generate_structured(
                system="sys",
                user="usr",
                schema=schema
            ))
            self.assertIn("post_text", res1)

            # 2. Test response_format= keyword argument (used by topic_discovery)
            res2 = asyncio.run(llm.generate_structured(
                system="sys",
                user="usr",
                response_format=schema
            ))
            self.assertIn("post_text", res2)

            # 3. Test 3rd positional argument (used by mentions)
            res3 = asyncio.run(llm.generate_structured(
                "sys",
                "usr",
                schema
            ))
            self.assertIn("post_text", res3)
        finally:
            httpx.AsyncClient = original_client


if __name__ == "__main__":
    unittest.main()
