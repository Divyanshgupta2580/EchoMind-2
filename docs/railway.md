# Deploying to Railway: Autonomous AI & Technology Persona

This guide covers deploying the Autonomous AI Persona application to Railway with volume persistence.

---

## 1. Prerequisites
- Railway account ([railway.app](https://railway.app))
- OpenRouter API key ([openrouter.ai](https://openrouter.ai))

---

## 2. Deploying on Railway

### Step 1: Deploy from GitHub
1. In Railway, click **New Project** → **Deploy from GitHub repo**.
2. Select your repository.
3. Railway automatically detects the `Dockerfile` and builds the service.

### Step 2: Configure Environment Variables
In the **Variables** tab, configure:

| Variable | Value / Description |
| :--- | :--- |
| `OPENROUTER_API_KEY` | `sk-or-v1-...` |
| `AGENT_DB_PATH` | `/data/agent_memory.db` |
| `PORT` | `8080` (Railway injects `$PORT` automatically) |

### Step 3: Add Persistent Volume
1. In the service settings, add a **Volume**.
2. Mount Path: `/data`.

---

## 3. Verifying the Deployment

Test the evaluator endpoints:
```bash
# 1. Initialize Persona
curl -X POST https://your-railway-app.up.railway.app/api/agent/init \
  -H "Content-Type: application/json" \
  -d '{"persona": {"name": "Ada", "domain": "AI Security"}}'

# 2. Query Feed
curl "https://your-railway-app.up.railway.app/api/agent/feed?agentId=<agentId>"
```
