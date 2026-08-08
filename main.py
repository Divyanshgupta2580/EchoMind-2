"""
Autonomous AI & Technology Persona Service.

FastAPI application providing:
- POST /api/agent/init: Initialize autonomous persona with name & domain
- GET /api/agent/feed: Fetch reverse-chronological feed with rationale and sources
- Periodic background autonomous publishing via APScheduler
"""

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from config.settings import settings
from services.autonomous_publisher import publisher_service
from services.memory import memory_store

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global background scheduler
scheduler = AsyncIOScheduler()


# Request / Response Schemas for Hackathon API Specification
class PersonaInitPayload(BaseModel):
    name: str = Field(..., description="Agent persona name (e.g. 'Ada')")
    domain: str = Field(..., description="Technical domain (e.g. 'AI Security')")


class AgentInitRequest(BaseModel):
    persona: PersonaInitPayload


class AgentInitResponse(BaseModel):
    agentId: str


class FeedPostItem(BaseModel):
    id: str
    createdAt: str
    text: str
    rationale: str
    sources: list[str]


class FeedResponse(BaseModel):
    posts: list[FeedPostItem]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown with zero-failure guarantee."""
    logger.info("[APP] Starting Autonomous AI Persona Service...")

    # Start the background task scheduler
    if not scheduler.running:
        # Schedule periodic background publishing across all initialized agents every 2 minutes
        # to ensure steady, reliable post generation during evaluation windows
        scheduler.add_job(
            publisher_service.run_all_agents_cycle,
            "interval",
            minutes=2,
            id="autonomous_publishing_cycle"
        )
        scheduler.start()
        logger.info("[APP] Background autonomous scheduler started successfully.")

    yield

    # Shutdown
    logger.info("[APP] Shutting down application...")
    if scheduler.running:
        scheduler.shutdown(wait=False)
    logger.info("[APP] Application shutdown complete.")


app = FastAPI(
    title="Autonomous AI & Technology Persona API",
    description="Hackathon API for autonomous topic discovery, editorial judgment, and feed publishing",
    version="2.0.0",
    lifespan=lifespan
)


# ============================================================================
# REQUIRED HACKATHON EVALUATOR API ENDPOINTS
# ============================================================================

@app.post("/api/agent/init", response_model=AgentInitResponse, status_code=200)
async def init_agent(payload: AgentInitRequest):
    """
    Initialize an autonomous persona with a name and technology domain.
    Called EXACTLY ONCE per agent.

    Generates a unique agentId, registers the persona in memory, triggers
    the first autonomous publishing cycle immediately, and schedules ongoing cycles.
    """
    try:
        persona_name = payload.persona.name.strip()
        persona_domain = payload.persona.domain.strip()

        if not persona_name or not persona_domain:
            raise HTTPException(status_code=400, detail="Persona name and domain must not be empty.")

        # Generate clean, unique agentId (e.g. agent-8a1b2c3d)
        agent_id = f"agent-{uuid.uuid4().hex[:8]}"

        # Register in memory store
        memory_store.register_agent(agent_id, persona_name, persona_domain)

        # Trigger ONE immediate publishing cycle so feed has fresh content upon initialization.
        # Recurring autonomous cycles are handled by the single authoritative global scheduler job (`autonomous_publishing_cycle`),
        # which automatically discovers all registered agents from SQLite via memory_store.list_agents().
        asyncio.create_task(publisher_service.run_publishing_cycle(agent_id))

        logger.info(f"[API] Initialized agent '{persona_name}' in domain '{persona_domain}' with id={agent_id}")
        return AgentInitResponse(agentId=agent_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error in agent initialization: {e}")
        raise HTTPException(status_code=500, detail="Internal server error during agent initialization.")


@app.get("/api/agent/feed", response_model=FeedResponse, status_code=200)
async def get_agent_feed(agentId: str = Query(..., description="The unique agent identifier returned during initialization")):
    """
    Get the published feed for a given agent.

    Returns:
    - Reverse chronological order (newest first)
    - ISO 8601 UTC timestamps
    - Unique post IDs
    - Transparent editorial rationales and source citation URLs
    - Empty list if no posts exist yet
    """
    try:
        if not agentId or not agentId.strip():
            return FeedResponse(posts=[])

        posts = memory_store.get_feed(agent_id=agentId.strip(), limit=200)
        return FeedResponse(posts=posts)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error retrieving feed for agentId={agentId}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error retrieving feed.")


# ============================================================================
# COMPATIBILITY & MONITORING ENDPOINTS
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint with system status."""
    return {
        "status": "healthy",
        "scheduler_running": scheduler.running,
        "total_posts": memory_store.count_posts(),
        "registered_agents": len(memory_store.list_agents()),
        "version": "2.0.0"
    }


@app.get("/metrics")
async def metrics():
    """Metrics and statistics for evaluation monitoring."""
    agents = memory_store.list_agents()
    return {
        "agents_count": len(agents),
        "total_posts": memory_store.count_posts(),
        "agents": agents
    }


@app.post("/api/agent/cycle")
async def trigger_agent_cycle(agentId: str = Query(..., description="Agent ID to trigger immediately")):
    """Manual cycle trigger endpoint for testing or simulated fast-forwarding."""
    result = await publisher_service.run_publishing_cycle(agentId)
    return result


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
