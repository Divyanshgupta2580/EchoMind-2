# Autonomous AI & Technology Persona

Autonomous AI and technology persona framework built for continuous topic discovery, rigorous editorial evaluation, and feed publishing.

---

## 1. Hackathon Objective & Features

The agent operates completely autonomously after initialization with **zero human prompts or external API triggers from the evaluator**:

1. **Live Topic Discovery**: Discovers candidate topics from live information sources (live web search with citation parsing and domain discovery feeds).
2. **Editorial Judgment**: Applies strict rejection criteria (domain mismatch, duplicate/repetitive, low information value/hype, weak source quality, lack of timeliness) and logs all rejections to memory.
3. **Consistent Persona**: Maintains an authoritative AI/technology identity (AI Security Researcher, ML Systems Engineer, Robotics Engineer, AI Product Analyst, etc.).
4. **Resilient Memory**: Tracks published posts, topic hashes, timestamps, transparent rationales, and source URLs.
5. **Autonomous Publishing Over Time**: Scheduled background worker (`APScheduler`) publishes continuous insights.
6. **Transparent Reasoning**: Every post provides detailed rationale on why the topic was chosen over rejected candidates.

---

## 2. API Reference

### `POST /api/agent/init`
Called **EXACTLY ONCE** to initialize the persona and start autonomous background publishing.

#### Request:
```json
{
  "persona": {
    "name": "Ada",
    "domain": "AI Security"
  }
}
```

#### Response (200 OK):
```json
{
  "agentId": "agent-8a1b2c3d"
}
```

---

### `GET /api/agent/feed?agentId=...`
Periodically called to fetch the agent's published feed.

#### Response (200 OK):
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

- **Order:** Strictly reverse-chronological (newest first).
- **Timestamps:** ISO 8601 UTC format (`YYYY-MM-DDTHH:MM:SSZ`).
- **Empty Feed:** Returns `{"posts": []}` when empty.

---

## 3. Architecture & Execution Flow

```
[ POST /api/agent/init ]
         │
         ▼
[ Register Agent in Memory ] ────► [ Trigger Immediate Cycle ]
         │                                   │
         ▼                                   ▼
[ APScheduler (every 2m) ] ───► [ Discover Candidate Topics (Web Search) ]
                                             │
                                             ▼
                                [ Editorial Judgment & Rejection ]
                                             │
                                             ▼
                                [ Save Post + Rationale + Sources ]
                                             │
                                             ▼
                                [ GET /api/agent/feed?agentId=... ]
```

---

## 4. Setup & Running Locally

### Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Key configuration:
- `OPENROUTER_API_KEY`: OpenRouter API key for LLM and live web search.

### Installation & Launch
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```
Server starts on `http://0.0.0.0:8080`.
