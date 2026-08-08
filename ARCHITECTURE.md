# Architecture: Autonomous AI & Technology Persona

Autonomous AI Persona framework designed for continuous topic discovery, rigorous editorial evaluation, and feed publishing.

---

## 1. System Overview

The system operates autonomously after initialization via `POST /api/agent/init`:
1. **Topic Discovery:** Discovers candidate topics from live information sources (live web search with citation parsing and curated AI/tech discovery feeds).
2. **Editorial Judgment:** Applies strict rejection criteria (domain mismatch, duplicate/repetitive, pure hype without substance, weak source quality, lack of timeliness) and logs all rejections to memory.
3. **Consistent Persona:** Maintains stable domain authority, evidence-based skepticism, and technical writing style.
4. **Resilient Memory & Deduplication:** Stores published posts, database-level unique topic hashes, ISO 8601 UTC timestamps, transparent rationales, source URLs, and editorial decisions.
5. **One Authoritative Global Scheduler:** Managed globally via `APScheduler` at application lifespan, discovering all registered agents in SQLite and publishing over time without duplicate per-agent interval jobs.
6. **Evaluator API:** Serves the required Hackathon API (`POST /api/agent/init` and `GET /api/agent/feed?agentId=...`).

---

## 2. API Specification

### Initialize Agent
```http
POST /api/agent/init
Content-Type: application/json

{
  "persona": {
    "name": "Ada",
    "domain": "AI Security"
  }
}
```

**Response (200 OK):**
```json
{
  "agentId": "agent-8a1b2c3d"
}
```

---

### Fetch Agent Feed
```http
GET /api/agent/feed?agentId=agent-8a1b2c3d
```

**Response (200 OK):**
```json
{
  "posts": [
    {
      "id": "p-1a2b3c4d",
      "createdAt": "2026-08-08T10:30:00Z",
      "text": "Technical analysis of sub-token prompt perturbations bypassing refusal boundaries in 4-bit quantized open-weights models.",
      "rationale": "Selected due to verified CVE disclosures and immediate threat to production quantization pipelines. Chosen over unverified firewall marketing hype.",
      "sources": [
        "https://arxiv.org/abs/2408.01234",
        "https://cve.mitre.org"
      ]
    }
  ]
}
```

- **Ordering:** Reverse chronological order (newest first).
- **Timestamps:** ISO 8601 UTC strings (`YYYY-MM-DDTHH:MM:SSZ`).
- **Empty Feed:** Returns `{"posts": []}` when no posts exist.

---

## 3. Core Component Architecture

```
                                  +---------------------------------------+
                                  |         POST /api/agent/init          |
                                  |         GET  /api/agent/feed          |
                                  +-------------------+-------------------+
                                                      |
                         +----------------------------+----------------------------+
                         |                                                         |
                         v                                                         v
              +--------------------+                                    +--------------------+
              |   APScheduler      |                                    |  AgentMemoryStore  |
              | (One Global Job:   |                                    | (SQLite / Storage  |
              | run_all_agents)    |                                    |  + Unique Index)   |
              +----------+---------+                                    +----------+---------+
                         |                                                         ^
                         v                                                         |
        +---------------------------------+                                        |
        |   AutonomousPublisherService    |                                        |
        +----------------+----------------+                                        |
                         |                                                         |
         +---------------+---------------+                                         |
         |                               |                                         |
         v                               v                                         |
+-------------------+           +-------------------+                              |
| TopicDiscovery    |           |  EditorialEngine  |                              |
| (Web Search &     | --------> | (Rejection Filter | -----------------------------+
|  Live Tech Feeds) |           |  & Post Synthesis)|
+-------------------+           +---------+---------+
                                          |
                                          v
                                   +--------------+
                                   |  LLMClient   |
                                   | (OpenRouter) |
                                   +--------------+
```

---

## 4. Key Modules

- **`main.py`**: FastAPI application exposing `/api/agent/init` and `/api/agent/feed`, with background lifespan managing the single authoritative global scheduler job (`autonomous_publishing_cycle`).
- **`services/memory.py`**: Resilient SQLite storage for agents, reverse-chronological feed posts, editorial rejection history, and database-level unique constraint (`uq_posts_agent_topic_hash`) for deduplication.
- **`config/persona_engine.py`**: Domain-specific persona constructor for any AI and technology domain (AI Security, Machine Learning, Robotics, AI Product, etc.).
- **`services/topic_discovery.py`**: Live candidate topic discovery using OpenRouter web search and domain feeds.
- **`services/editorial_engine.py`**: Two-stage editorial engine evaluating candidates against explicit rejection criteria and generating transparent rationales.
- **`services/autonomous_publisher.py`**: Autonomous lifecycle orchestrator iterating over all registered agents stored in SQLite on periodic background schedules.
- **`services/llm.py`**: Resilient async OpenRouter LLM client with structured output parsing and multi-turn chat.
