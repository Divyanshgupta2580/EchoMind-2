# PROMPT_HISTORY.md — AI Tool Usage & Development History

> **VicoDathon 2026 — Autonoma**
> This document details our use of AI coding assistants throughout the development of this project.

---

## AI Tools Used

| Tool | Model | Purpose |
|------|-------|---------|
| **Antigravity IDE** (Gemini-powered agent) | Gemini 3.1 Pro / Claude Opus 4.6 | Primary pair-programming assistant for architecture design, code generation, debugging, and documentation |
| **OpenRouter API** | Gemini 2.5 Flash | Runtime LLM for live web search, topic extraction, editorial scoring, semantic deduplication, and post synthesis |

---

## How AI Was Used in Each Phase

### Phase 1: Architecture Design — Background Loop Decoupling

**Problem**: The initial prototype called the LLM on every `GET /api/agent/feed` request. This meant the evaluator's automated grading script would trigger expensive LLM calls with every polling request, risking rate limits and unpredictable latency.

**AI-Assisted Solution**: We used the AI assistant to architect a decoupled read/write architecture:
- **Reads** (`GET /feed`): Pure SQLite queries with zero LLM involvement, returning pre-computed JSON in < 5ms.
- **Writes** (Background): An `APScheduler` `AsyncIOScheduler` job runs every ~45 minutes (±5 min jitter), performing discovery → scoring → deduplication → synthesis → SQLite insert.

The AI assistant helped design the `AutonomousPublisherService` class with per-agent `asyncio.Lock` guards and atomic CAS (Compare-And-Swap) window transitions to prevent race conditions during concurrent evaluation.

### Phase 2: SQLite Concurrent Write Safety

**Problem**: The `APScheduler` background thread writes to SQLite while the REST API reads simultaneously. Default SQLite locks the entire database during writes, causing `database is locked` errors under concurrent load.

**AI-Assisted Solution**: The assistant recommended and implemented:
```python
conn = sqlite3.connect(db_path, timeout=15, check_same_thread=False)
conn.execute("PRAGMA journal_mode=WAL;")
conn.execute("PRAGMA busy_timeout=15000;")
```

WAL (Write-Ahead Logging) mode allows concurrent readers alongside a single writer. We verified this under stress testing: 150 concurrent read requests during active writes with **0 lock errors**.

### Phase 3: LLM Editorial Prompt Engineering

**Problem**: Early prompts produced generic, low-quality outputs. The LLM would summarize web search results but omit source URLs, generate posts exceeding 280 characters, and fail to distinguish between high-impact CVE disclosures and marketing press releases.

**AI-Assisted Solution**: The assistant helped craft specialized prompts at each pipeline stage:

1. **Topic Extraction Prompt**: Uses strict JSON Schema (`response_format`) with required `source_urls: list[str]` field and explicit instruction: _"You MUST extract the exact HTTP/HTTPS source URLs from the search results."_

2. **Semantic Deduplication Prompt**: Injects the last 10 published post titles into the system prompt with instruction: _"Reject this topic if it is conceptually identical to any of these previously published posts."_

3. **Post Synthesis Prompt**: Enforces the 280-character constraint, requires 3-part rationale (why selected, why relevant now, why chosen over alternatives), and mandates non-empty source URL array.

All structured outputs use OpenRouter's `response_format` with `"strict": true` JSON Schema enforcement.

### Phase 4: Deterministic Scoring Engine

**Problem**: Relying solely on LLM judgment for candidate quality is non-deterministic and difficult to debug.

**AI-Assisted Solution**: The assistant designed a deterministic 6-criteria scoring engine (0–100 points) that evaluates candidates before any LLM synthesis:
- Recency (0–20), Significance (0–25), Domain Relevance (0–20), Source Quality (0–15), Novelty (0–10), Verifiability (0–10)
- Minimum publishing threshold: 75.0/100
- All scores and rejection reasons are persisted to SQLite for full auditability

### Phase 5: Codebase Pruning & Hardening

**Problem**: The original codebase contained legacy Twitter/X API integrations, `tweepy` dependencies, `asyncpg` PostgreSQL drivers, and unused social media publishing infrastructure that were irrelevant to the hackathon evaluation rubric.

**AI-Assisted Solution**: The assistant performed systematic pruning:
- Removed 25+ files (Twitter publisher, mention handlers, unified agent, legacy tools, personality configs)
- Stripped `tweepy`, `asyncpg`, and social media SDK dependencies from `requirements.txt`
- Simplified `tools/registry.py` to only support `web_search`
- Hardened the Dockerfile with `--platform=linux/amd64` for cross-platform compatibility

### Phase 6: API Contract Hardening

**Problem**: The automated grading script requires exact JSON structure compliance. A single mismatched field name or data type causes a zero score.

**AI-Assisted Solution**: The assistant audited and hardened every endpoint:
- `POST /api/agent/init` → Returns exactly `{"agentId": "..."}` with zero extraneous fields
- `GET /api/agent/feed` → Returns `{"posts": [...]}` with ISO 8601 UTC `createdAt` (ending in `Z`), `sources` as `list[str]`, newest-first ordering, and `{"posts": []}` for empty databases
- Added alias routes (`/feed`, `/api/feed`) for compatibility with different evaluator URL patterns

### Phase 7: Stress Testing & QA

**AI-Assisted Solution**: The assistant created a comprehensive time-compressed stress test (`tests/stress_test_qa.py`) that simulates 48 hours of evaluation in under 10 seconds:
- Clean slate database initialization
- Agent initialization and window lifecycle
- 4-candidate editorial evaluation (2 accepted, 1 rejected for low score, 1 rejected as duplicate)
- Leader selection and post publication
- 150 concurrent read requests during active writes (WAL verification)
- Full JSON schema compliance validation

---

## Summary

AI coding assistants were used throughout every phase of development — from initial architecture decisions through final stress testing. The most impactful contributions were:

1. **Architectural**: Decoupling the read API from the LLM write loop (the single most important design decision)
2. **Concurrency**: SQLite WAL mode configuration for zero-lock concurrent access
3. **Prompt Engineering**: Structured JSON Schema enforcement for reliable source URL extraction
4. **Quality Assurance**: Automated stress tests verifying the complete evaluation lifecycle

All code was reviewed, understood, and validated by the development team before integration.
