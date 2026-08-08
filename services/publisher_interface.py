"""
Publisher Interface Abstraction.

Defines the contract for publishing news posts to external social platforms (such as X/Twitter)
or test mocks, decoupling the autonomous publishing pipeline from concrete API implementations.
"""

from abc import ABC, abstractmethod
from typing import Any


class IXPublisher(ABC):
    """Abstract interface for external social media publishing."""

    @abstractmethod
    async def publish_post(
        self,
        text: str,
        metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Publish a post to the external platform.

        Args:
            text: Formatted post content (must adhere to platform character limits).
            metadata: Optional dictionary with auxiliary context (e.g. source URLs, candidate_id, window_id).

        Returns:
            Dictionary containing publishing status, post_id, published_at, and platform details:
            {
                "success": bool,
                "status": "PUBLISHED" | "FAILED" | "NOT_ATTEMPTED",
                "post_id": str | None,
                "text": str,
                "error": str | None
            }
        """
        pass
