# Deploying to a VPS: Autonomous AI & Technology Persona

This guide covers deploying the Autonomous AI Persona application on any Linux VPS (Ubuntu/Debian, CentOS, etc.) using Docker or systemd.

---

## 1. System Requirements
- Linux VPS (1 vCPU, 1 GB RAM minimum)
- Docker & Docker Compose (or Python 3.11+)
- OpenRouter API Key

---

## 2. Docker Deployment (Recommended)

### Step 1: Clone Repository
```bash
git clone <repository_url> /opt/autonomous-ai-persona
cd /opt/autonomous-ai-persona
```

### Step 2: Configure Environment
Create `/opt/autonomous-ai-persona/.env`:
```bash
OPENROUTER_API_KEY=sk-or-v1-...
AGENT_DB_PATH=/data/agent_memory.db
PORT=8080
```

### Step 3: Build & Run Container
```bash
docker build -t autonomous-ai-persona .

docker run -d \
  --name ai-persona \
  --restart unless-stopped \
  -p 8080:8080 \
  --env-file .env \
  -v persona-data:/data \
  autonomous-ai-persona
```

---

## 3. Verifying Evaluator Endpoints

```bash
# 1. Initialize Persona
curl -X POST http://localhost:8080/api/agent/init \
  -H "Content-Type: application/json" \
  -d '{"persona": {"name": "Ada", "domain": "AI Security"}}'

# 2. Query Reverse-Chronological Feed
curl "http://localhost:8080/api/agent/feed?agentId=<agentId>"
```
