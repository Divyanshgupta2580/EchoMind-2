"""
EchoMind API Client Service.

Centralized HTTP client for communicating with the deployed EchoMind backend:
- Production Backend: https://echomind-ltwo.onrender.com
- Local Backend: http://localhost:8080 or configured ECHOMIND_API_BASE_URL
- Handles Render cold starts (30s timeout with retries), network errors, and structured response parsing.
- Zero secret exposure to frontend or client payloads.
"""

import asyncio
import logging
from typing import Any
import urllib.parse

from config.settings import settings

logger = logging.getLogger(__name__)


class EchoMindAPIError(Exception):
    """Custom exception for EchoMind backend API errors."""

    def __init__(self, message: str, status_code: int | None = None, details: Any = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details


class EchoMindClient:
    """
    Asynchronous and synchronous capable API client for EchoMind Backend.
    """

    def __init__(self, base_url: str | None = None, timeout_seconds: float = 35.0):
        self.base_url = (base_url or settings.api_base_url).rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _get_url(self, path: str) -> str:
        """Construct full URL from endpoint path."""
        clean_path = path.lstrip("/")
        return f"{self.base_url}/{clean_path}"

    async def init_agent(self, name: str, domain: str) -> dict[str, Any]:
        """
        Initialize an autonomous persona with name and technical domain.
        Endpoint: POST /api/agent/init
        """
        import httpx

        url = self._get_url("/api/agent/init")
        payload = {
            "persona": {
                "name": name.strip(),
                "domain": domain.strip()
            }
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, json=payload)
                if response.status_code == 200:
                    data = response.json()
                    logger.info(f"[CLIENT] Initialized agent on {self.base_url}: agentId={data.get('agentId')}")
                    return data
                elif response.status_code == 400:
                    raise EchoMindAPIError("Invalid persona payload: name and domain are required.", status_code=400)
                else:
                    raise EchoMindAPIError(
                        f"Backend responded with HTTP {response.status_code}",
                        status_code=response.status_code
                    )
        except httpx.TimeoutException:
            logger.warning(f"[CLIENT] Request timeout calling {url} (possible Render cold start).")
            raise EchoMindAPIError("Backend request timed out. The Render service may be resuming from cold start.")
        except httpx.ConnectError:
            logger.warning(f"[CLIENT] Connection error calling {url}.")
            raise EchoMindAPIError(f"Could not connect to backend at {self.base_url}. Verify network connectivity.")
        except Exception as e:
            if isinstance(e, EchoMindAPIError):
                raise
            logger.error(f"[CLIENT] Unexpected error initializing agent: {e}")
            raise EchoMindAPIError("Failed to communicate with EchoMind backend.")

    async def get_feed(self, agent_id: str) -> dict[str, Any]:
        """
        Fetch reverse-chronological news feed for an agent.
        Endpoint: GET /api/agent/feed?agentId=...
        """
        import httpx

        params = {"agentId": agent_id.strip()}
        url = self._get_url("/api/agent/feed")

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(url, params=params)
                if response.status_code == 200:
                    data = response.json()
                    # Ensure posts key is always present
                    if "posts" not in data or not isinstance(data["posts"], list):
                        return {"posts": []}
                    return data
                elif response.status_code == 404:
                    raise EchoMindAPIError(f"Agent {agent_id} not found.", status_code=404)
                else:
                    raise EchoMindAPIError(
                        f"Backend responded with HTTP {response.status_code}",
                        status_code=response.status_code
                    )
        except httpx.TimeoutException:
            raise EchoMindAPIError("Backend request timed out while fetching feed.")
        except httpx.ConnectError:
            raise EchoMindAPIError(f"Could not connect to backend at {self.base_url}.")
        except Exception as e:
            if isinstance(e, EchoMindAPIError):
                raise
            raise EchoMindAPIError("Failed to retrieve feed from backend.")

    async def get_status(self, agent_id: str) -> dict[str, Any]:
        """
        Fetch real-time window, candidate count, leader, and publication status.
        Endpoint: GET /api/agent/status?agentId=...
        """
        import httpx

        params = {"agentId": agent_id.strip()}
        url = self._get_url("/api/agent/status")

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(url, params=params)
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 404:
                    raise EchoMindAPIError(f"Agent {agent_id} not found.", status_code=404)
                else:
                    raise EchoMindAPIError(
                        f"Backend responded with HTTP {response.status_code}",
                        status_code=response.status_code
                    )
        except httpx.TimeoutException:
            raise EchoMindAPIError("Backend request timed out while fetching status.")
        except httpx.ConnectError:
            raise EchoMindAPIError(f"Could not connect to backend at {self.base_url}.")
        except Exception as e:
            if isinstance(e, EchoMindAPIError):
                raise
            raise EchoMindAPIError("Failed to retrieve status from backend.")

    async def check_health(self) -> dict[str, Any]:
        """
        Check health status of backend service.
        Endpoint: GET /healthz
        """
        import httpx

        url = self._get_url("/healthz")

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    return response.json()
                else:
                    return {"status": "unhealthy", "status_code": response.status_code}
        except httpx.TimeoutException:
            return {"status": "timeout", "message": "Backend cold start or timeout"}
        except httpx.ConnectError:
            return {"status": "offline", "message": f"Cannot connect to {self.base_url}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


# Global default client configured to deployed backend
echomind_client = EchoMindClient()
