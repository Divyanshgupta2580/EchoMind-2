# Deploying to Render: Autonomous AI & Technology Persona

This guide covers deploying the Autonomous AI Persona application to Render as a Web Service with persistent storage.

---

## 1. Prerequisites
- Render account ([render.com](https://render.com))
- OpenRouter API key ([openrouter.ai](https://openrouter.ai))

---

## 2. Deploying on Render

### Step 1: Create Web Service
1. In Render Dashboard, click **New +** → **Web Service**.
2. Connect your GitHub repository.
3. Configure the service:
   - **Name:** `autonomous-ai-persona`
   - **Region:** Any region (e.g. Oregon, Frankfurt)
   - **Branch:** `main`
   - **Runtime:** `Docker` (or `Python 3` with Build Command: `pip install -r requirements.txt` and Start Command: `python main.py`)
   - **Plan:** Free or Starter

### Step 2: Configure Environment Variables
In the **Environment** tab, set:

| Variable | Value / Description | Required? |
| :--- | :--- | :--- |
| `OPENROUTER_API_KEY` | `sk-or-v1-...` | **Yes** (for live LLM inference & web search) |
| `AGENT_DB_PATH` | `/data/agent_memory.db` | **Yes** (when using persistent disk) |
| `PORT` | `8080` | Render default is injected automatically |

### Step 3: Attach Persistent Disk (Recommended)
1. Go to the **Disks** section in your Web Service settings.
2. Click **Add Disk**:
   - **Name:** `persona-data`
   - **Mount Path:** `/data`
   - **Size:** `1 GB` (Standard)
3. Click **Save**.

---

## 3. Verifying the Deployment

Once the service is Live, verify via the evaluator API:

### 1. Initialize Persona (POST /api/agent/init)
```bash
curl -X POST https://autonomous-ai-persona.onrender.com/api/agent/init \
  -H "Content-Type: application/json" \
  -d '{
    "persona": {
      "name": "Ada",
      "domain": "AI Security"
    }
  }'
```
**Expected Response (200 OK):**
```json
{
  "agentId": "agent-8a1b2c3d"
}
```

### 2. Retrieve Feed (GET /api/agent/feed)
```bash
curl "https://autonomous-ai-persona.onrender.com/api/agent/feed?agentId=agent-8a1b2c3d"
```
**Expected Response (200 OK):**
```json
{
  "posts": [
    {
      "id": "p-1a2b3c4d",
      "createdAt": "2026-08-08T10:00:00Z",
      "text": "...",
      "rationale": "...",
      "sources": ["https://..."]
    }
  ]
}
```
