# API Keys and Configuration Guide

This guide explains how to configure API keys for running the Autonomous AI & Technology Persona.

---

## 1. OpenRouter API Key (Required for Live Operations)

OpenRouter provides access to frontier LLMs and real-time live web search plugins through a single unified API.

### Obtaining Your Key:
1. Go to [openrouter.ai](https://openrouter.ai)
2. Sign up and navigate to **Keys** (`openrouter.ai/keys`)
3. Create a new API key and copy its value.
4. Add it to your `.env` file or environment variables:
   ```bash
   OPENROUTER_API_KEY=sk-or-v1-...
   ```

---

## 2. Persistent Storage Configuration

The system uses SQLite with WAL mode by default.

```bash
# Optional: Set custom path for SQLite database
AGENT_DB_PATH=/data/agent_memory.db
```

---

## 3. Optional / Legacy Twitter Integration

> [!NOTE]
> Twitter API integration is legacy/out of scope for the AI Autonomy Hackathon specification. The autonomous persona operates completely independently via the Evaluator API (`/api/agent/init` and `/api/agent/feed`).

If running legacy Twitter posting mode:
- `TWITTER_API_KEY`: Consumer API Key
- `TWITTER_API_SECRET`: Consumer API Secret
- `TWITTER_ACCESS_TOKEN`: User Access Token
- `TWITTER_ACCESS_SECRET`: User Access Secret
- `TWITTER_BEARER_TOKEN`: Application Bearer Token
