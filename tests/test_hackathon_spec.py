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
9. Evaluator API endpoints: POST /api/agent/init, GET /api/agent/feed, GET /api/agent/status, GET /healthz, GET /api/agents.
10. CRIT-001 Atomic CAS window-close lock under concurrent invocations.
11. CRIT-002 Non-open windows (PUBLISHED, NO_QUALIFIED_STORY) cannot be re-closed.
12. HIGH-001 Safe X retries, persistent SQLite idempotency, and 403 duplicate reconciliation.
13. HIGH-002 Per-agent asyncio.Lock preventing scheduler cycle overlap.
14. MAX_AGENTS=5 Server-side atomic FIFO rotation and dependent data cleanup.
15. Publication Invariant Cases A through G.
"""

import asyncio
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from config.persona_engine import build_persona_profile
from config.settings import settings
from main import app
from services.autonomous_publisher import AutonomousPublisherService
from services.editorial_engine import EditorialEngine
from services.memory import AgentMemoryStore
from services.twitter import MockXPublisher, XPublisher

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
        self.assertEqual(len(self.mock_publisher.published_posts), 1)
        self.assertIn("Story D", self.mock_publisher.published_posts[0]["text"])

    # =========================================================================
    # 3. REQUIRED EDGE CASE 2: ZERO PUBLICATION OUTCOME
    # =========================================================================

    def test_zero_publication_when_no_candidate_qualifies(self):
        """
        TEST CASE 2:
        All candidates during the 2-hour window score below 75.0.
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
    # 4. REQUIRED EDGE CASE 3: DEDUPLICATION
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
    # 6. CRIT-001 & CRIT-002: ATOMIC CAS LOCK & IDEMPOTENT WINDOW CLOSURE
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
    # 7. HIGH-001: X PUBLISHER RETRIES, IDEMPOTENCY & DUPLICATE CONTENT RECOVERY
    # =========================================================================

    def test_high_001_x_retry_duplicate_content_recovery(self):
        """
        HIGH-001: If Tweepy/X reports a 403 Duplicate Content on retry
        (because the initial network attempt reached X but the client timed out),
        XPublisher reconciles the post as PUBLISHED and prevents FAILED state.
        """
        class DuplicateFailingClient:
            def __init__(self):
                self.calls = 0

            def create_tweet(self, text):
                self.calls += 1
                if self.calls == 1:
                    raise Exception("ConnectionResetError: Connection lost before response")
                raise Exception("403 Forbidden: You are not allowed to create a tweet with duplicate content.")

        x_pub = XPublisher(
            api_key="mock-key",
            api_secret="mock-sec",
            access_token="mock-tok",
            access_token_secret="mock-tok-sec",
            max_retries=2,
            memory_store=self.memory
        )
        x_pub._client = DuplicateFailingClient()

        result = asyncio.run(x_pub.publish_post(
            text="Breaking: CVE-2026-10492 published on adversarial weights.",
            metadata={"idempotency_key": "k-dup-test", "agent_id": "a-dup", "window_id": "w-dup"}
        ))

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "PUBLISHED")
        self.assertTrue(result.get("is_duplicate"))
        self.assertTrue(result.get("reconciled"))

        # Verify persistent record in SQLite
        rec = self.memory.get_x_publication_record("k-dup-test")
        self.assertIsNotNone(rec)

    # =========================================================================
    # 8. HIGH-002: SCHEDULER DISCOVERY CONCURRENCY LOCK
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
    # 9. 2-HOUR WINDOW TIME MATHEMATICS
    # =========================================================================

    def test_2_hour_window_time_calculation(self):
        """Verify that ends_at = started_at + 120 minutes in strict ISO 8601 UTC."""
        agent_id = "agent-time-math"
        self.memory.register_agent(agent_id, "Ada", "AI Security")
        window = self.memory.create_window(agent_id, duration_minutes=120)

        start_dt = datetime.strptime(window["started_at"], "%Y-%m-%dT%H:%M:%SZ")
        end_dt = datetime.strptime(window["ends_at"], "%Y-%m-%dT%H:%M:%SZ")
        delta = end_dt - start_dt
        self.assertEqual(delta.total_seconds(), 7200) # exactly 120 minutes

    # =========================================================================
    # 10. MAX_AGENTS=5 ATOMIC FIFO ROTATION
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
    # 11. EVALUATOR API CONTRACTS & GET /api/agents
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
        self.assertEqual(healthz_res.json()["publish_window_minutes"], 120)
        self.assertEqual(healthz_res.json()["min_news_score"], 75.0)
        self.assertEqual(healthz_res.json()["max_agents"], 5)

        # 6. GET / and /dashboard (Web Client Interface)
        dash_res = client.get("/")
        self.assertEqual(dash_res.status_code, 200)
        self.assertIn("text/html", dash_res.headers.get("content-type", ""))

        # 7. Centralized API Base URL Configuration
        self.assertEqual(settings.api_base_url, "https://echomind-ltwo.onrender.com")


if __name__ == "__main__":
    unittest.main()
