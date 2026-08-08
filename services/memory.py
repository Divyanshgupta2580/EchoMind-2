"""
Resilient Memory and Feed Store for Autonomous Personas.

Provides thread-safe, resilient persistence using SQLite (built-in)
with support for PostgreSQL fallback, storing:
- Agents (agent_id, name, domain, created_at)
- Feed Posts (id, agent_id, created_at, text, rationale, sources, topic_hash)
- Editorial Decisions (agent_id, topic_title, decision, reason, evaluated_at)
- Topic Fingerprints / Hashes for deduplication
"""

import hashlib
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default SQLite database path in workspace
DEFAULT_DB_PATH = os.getenv("AGENT_DB_PATH", str(Path(__file__).parent.parent / "agent_memory.db"))


class AgentMemoryStore:
    """
    Asynchronous and synchronous capable persistent memory store for autonomous agents.
    Uses SQLite with WAL mode for concurrency, durability, and zero external dependency.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._ensure_parent_dir()
        self._init_db()

    def _ensure_parent_dir(self) -> None:
        """Ensure parent directory exists for configured database path."""
        try:
            parent_dir = Path(self.db_path).parent
            if str(parent_dir) not in ("", "."):
                parent_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"[MEMORY] Could not create parent directory for {self.db_path}: {e}")

    def _get_connection(self) -> sqlite3.Connection:
        self._ensure_parent_dir()
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for high concurrency
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            # Agents table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

            # Feed posts table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS feed_posts (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    text TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    topic_hash TEXT NOT NULL,
                    FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
                )
            """)

            # Editorial decisions & rejections table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS editorial_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    topic_title TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
                )
            """)

            # Create performance and uniqueness indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_agent_time ON feed_posts(agent_id, created_at DESC);")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_posts_agent_topic_hash ON feed_posts(agent_id, topic_hash);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_agent ON editorial_decisions(agent_id, evaluated_at DESC);")
            conn.commit()
            logger.info(f"[MEMORY] Initialized SQLite store at {self.db_path}")

    @staticmethod
    def compute_topic_hash(topic_text: str) -> str:
        """Compute normalized SHA-256 hash for topic deduplication."""
        normalized = "".join(c.lower() for c in topic_text if c.isalnum() or c.isspace()).strip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def register_agent(self, agent_id: str, name: str, domain: str) -> dict[str, Any]:
        """Register a new autonomous agent."""
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO agents (agent_id, name, domain, created_at) VALUES (?, ?, ?, ?)",
                (agent_id, name, domain, now_utc)
            )
            conn.commit()
        logger.info(f"[MEMORY] Registered agent '{name}' ({domain}) with id={agent_id}")
        return {"agentId": agent_id, "name": name, "domain": domain, "createdAt": now_utc}

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Get agent profile by agent_id."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
            if row:
                return {
                    "agentId": row["agent_id"],
                    "name": row["name"],
                    "domain": row["domain"],
                    "createdAt": row["created_at"]
                }
            return None

    def list_agents(self) -> list[dict[str, Any]]:
        """List all registered agents."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM agents ORDER BY created_at ASC").fetchall()
            return [
                {
                    "agentId": r["agent_id"],
                    "name": r["name"],
                    "domain": r["domain"],
                    "createdAt": r["created_at"]
                }
                for r in rows
            ]

    def save_post(
        self,
        agent_id: str,
        text: str,
        rationale: str,
        sources: list[str],
        topic_hash: str | None = None,
        created_at: str | None = None,
        post_id: str | None = None
    ) -> dict[str, Any]:
        """
        Save a published post to the feed.
        Enforces UTC ISO 8601 timestamps and unique post IDs.
        """
        if not post_id:
            # Generate short, unique post ID (e.g. p-a1b2c3d4)
            post_id = f"p-{uuid.uuid4().hex[:8]}"

        if not created_at:
            created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if not topic_hash:
            topic_hash = self.compute_topic_hash(text)

        sources_json = json.dumps(sources if isinstance(sources, list) else [])

        with self._get_connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO feed_posts (id, agent_id, created_at, text, rationale, sources_json, topic_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (post_id, agent_id, created_at, text, rationale, sources_json, topic_hash)
                )
                conn.commit()
                logger.info(f"[MEMORY] Saved post {post_id} for agent {agent_id}")
                return {
                    "id": post_id,
                    "createdAt": created_at,
                    "text": text,
                    "rationale": rationale,
                    "sources": sources,
                    "is_duplicate": False
                }
            except sqlite3.IntegrityError as e:
                logger.warning(f"[MEMORY] Duplicate post insertion blocked for agent {agent_id}, topic_hash {topic_hash}: {e}")
                # Query existing post for deterministic return
                row = conn.execute(
                    "SELECT id, created_at, text, rationale, sources_json FROM feed_posts WHERE agent_id = ? AND topic_hash = ?",
                    (agent_id, topic_hash)
                ).fetchone()
                if row:
                    try:
                        existing_sources = json.loads(row["sources_json"])
                    except Exception:
                        existing_sources = []
                    return {
                        "id": row["id"],
                        "createdAt": row["created_at"],
                        "text": row["text"],
                        "rationale": row["rationale"],
                        "sources": existing_sources,
                        "is_duplicate": True
                    }
                return None

    def get_feed(self, agent_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """
        Get posts in reverse chronological order (newest first).
        Returns list of posts with id, createdAt (ISO 8601 UTC), text, rationale, sources.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, text, rationale, sources_json
                FROM feed_posts
                WHERE agent_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (agent_id, limit)
            ).fetchall()

            posts = []
            for r in rows:
                try:
                    sources = json.loads(r["sources_json"])
                except Exception:
                    sources = []
                posts.append({
                    "id": r["id"],
                    "createdAt": r["created_at"],
                    "text": r["text"],
                    "rationale": r["rationale"],
                    "sources": sources
                })
            return posts

    def is_topic_covered(self, agent_id: str, topic_hash: str) -> bool:
        """Check if a topic hash has already been published by this agent."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM feed_posts WHERE agent_id = ? AND topic_hash = ? LIMIT 1",
                (agent_id, topic_hash)
            ).fetchone()
            return row is not None

    def get_recent_topic_hashes(self, agent_id: str, limit: int = 50) -> set[str]:
        """Get set of recently covered topic hashes."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT topic_hash FROM feed_posts WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
                (agent_id, limit)
            ).fetchall()
            return {r["topic_hash"] for r in rows}

    def log_editorial_decision(
        self,
        agent_id: str,
        topic_title: str,
        decision: str,
        reason: str
    ) -> None:
        """Log editorial decision (ACCEPTED or REJECTED) with rationale."""
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO editorial_decisions (agent_id, topic_title, decision, reason, evaluated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (agent_id, topic_title, decision, reason, now_utc)
            )
            conn.commit()
        logger.debug(f"[MEMORY] Logged decision for '{topic_title}': {decision} ({reason})")

    def get_recent_editorial_decisions(self, agent_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent editorial decisions and rejections for context."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT topic_title, decision, reason, evaluated_at
                FROM editorial_decisions
                WHERE agent_id = ?
                ORDER BY evaluated_at DESC
                LIMIT ?
                """,
                (agent_id, limit)
            ).fetchall()
            return [
                {
                    "topic": r["topic_title"],
                    "decision": r["decision"],
                    "reason": r["reason"],
                    "evaluatedAt": r["evaluated_at"]
                }
                for r in rows
            ]

    def count_posts(self, agent_id: str | None = None) -> int:
        """Count total published posts."""
        with self._get_connection() as conn:
            if agent_id:
                return conn.execute("SELECT COUNT(*) FROM feed_posts WHERE agent_id = ?", (agent_id,)).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM feed_posts").fetchone()[0]


# Global memory singleton instance
memory_store = AgentMemoryStore()
