"""
Official X / Twitter Publishing Implementation.

Provides:
1. IXPublisher abstraction interface adherence.
2. XPublisher: Tweepy API v2 integration with OAuth 1.0a / Bearer authentication,
   idempotency tracking, 280-character validation, and bounded retry policy.
3. MockXPublisher: In-memory mock publisher for automated testing and offline verification.
4. TwitterClient: Backwards-compatible Tweepy client wrapper.
"""

import asyncio
import hashlib
import logging
from typing import Any

from config.settings import settings
from services.publisher_interface import IXPublisher

logger = logging.getLogger(__name__)

# Maximum allowed characters for standard X / Twitter posts
MAX_X_POST_LENGTH = 280


class XPublisher(IXPublisher):
    """
    Production publisher for X/Twitter using official API v2 via Tweepy.
    Implements character limit validation, idempotency, and bounded retries.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        access_token: str | None = None,
        access_token_secret: str | None = None,
        bearer_token: str | None = None,
        max_retries: int = 3
    ):
        self.api_key = api_key or settings.x_api_key
        self.api_secret = api_secret or settings.x_api_secret
        self.access_token = access_token or settings.x_access_token
        self.access_token_secret = access_token_secret or settings.x_access_token_secret
        self.bearer_token = bearer_token or settings.x_bearer_token
        self.max_retries = max_retries
        self._published_idempotency_keys: dict[str, str] = {}
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazily initialize Tweepy client if credentials are configured."""
        if self._client is not None:
            return self._client

        import tweepy

        if not (self.api_key and self.api_secret and self.access_token and self.access_token_secret):
            logger.warning("[X_PUBLISHER] X API credentials not fully configured in environment.")

        self._client = tweepy.Client(
            bearer_token=self.bearer_token or None,
            consumer_key=self.api_key or None,
            consumer_secret=self.api_secret or None,
            access_token=self.access_token or None,
            access_token_secret=self.access_token_secret or None,
            wait_on_rate_limit=True
        )
        return self._client

    async def publish_post(
        self,
        text: str,
        metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Publish post to X with character validation, idempotency, and bounded retries.
        """
        metadata = metadata or {}
        trimmed_text = text.strip()

        # 1. Character Limit Validation
        if len(trimmed_text) > MAX_X_POST_LENGTH:
            logger.error(f"[X_PUBLISHER] Post exceeds {MAX_X_POST_LENGTH} characters ({len(trimmed_text)} chars).")
            return {
                "success": False,
                "status": "FAILED",
                "post_id": None,
                "text": trimmed_text,
                "error": f"Character limit exceeded: {len(trimmed_text)} > {MAX_X_POST_LENGTH}"
            }

        # 2. Idempotency Check
        idempotency_key = metadata.get("idempotency_key") or hashlib.sha256(trimmed_text.encode("utf-8")).hexdigest()[:16]
        if idempotency_key in self._published_idempotency_keys:
            existing_post_id = self._published_idempotency_keys[idempotency_key]
            logger.info(f"[X_PUBLISHER] Post already published (Idempotency Key: {idempotency_key} -> Post ID: {existing_post_id})")
            return {
                "success": True,
                "status": "PUBLISHED",
                "post_id": existing_post_id,
                "text": trimmed_text,
                "error": None,
                "is_duplicate": True
            }

        # 3. Execution with Bounded Retries
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                client = self._get_client()
                logger.info(f"[X_PUBLISHER] Publishing to X (attempt {attempt}/{self.max_retries}): {trimmed_text[:60]}...")
                
                # Execute Tweepy create_tweet
                response = client.create_tweet(text=trimmed_text)
                post_id = str(response.data["id"])

                self._published_idempotency_keys[idempotency_key] = post_id
                logger.info(f"[X_PUBLISHER] Successfully published to X! Post ID: {post_id}")
                return {
                    "success": True,
                    "status": "PUBLISHED",
                    "post_id": post_id,
                    "text": trimmed_text,
                    "error": None
                }
            except Exception as e:
                last_error = e
                logger.warning(f"[X_PUBLISHER] Attempt {attempt} failed: {e}")
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)

        logger.error(f"[X_PUBLISHER] All {self.max_retries} attempts failed to publish post: {last_error}")
        return {
            "success": False,
            "status": "FAILED",
            "post_id": None,
            "text": trimmed_text,
            "error": str(last_error)
        }


class MockXPublisher(IXPublisher):
    """
    In-memory mock publisher for automated tests and offline development.
    Guarantees zero external network requests.
    """

    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail
        self.published_posts: list[dict[str, Any]] = []
        self._idempotency_map: dict[str, str] = {}

    async def publish_post(
        self,
        text: str,
        metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        metadata = metadata or {}
        trimmed_text = text.strip()

        if len(trimmed_text) > MAX_X_POST_LENGTH:
            return {
                "success": False,
                "status": "FAILED",
                "post_id": None,
                "text": trimmed_text,
                "error": f"Character limit exceeded: {len(trimmed_text)} > {MAX_X_POST_LENGTH}"
            }

        if self.should_fail:
            return {
                "success": False,
                "status": "FAILED",
                "post_id": None,
                "text": trimmed_text,
                "error": "Simulated network timeout during X publish"
            }

        idempotency_key = metadata.get("idempotency_key") or hashlib.sha256(trimmed_text.encode("utf-8")).hexdigest()[:16]
        if idempotency_key in self._idempotency_map:
            return {
                "success": True,
                "status": "PUBLISHED",
                "post_id": self._idempotency_map[idempotency_key],
                "text": trimmed_text,
                "error": None,
                "is_duplicate": True
            }

        mock_id = f"x-mock-{len(self.published_posts) + 1:04d}"
        self._idempotency_map[idempotency_key] = mock_id
        record = {
            "success": True,
            "status": "PUBLISHED",
            "post_id": mock_id,
            "text": trimmed_text,
            "metadata": metadata,
            "error": None
        }
        self.published_posts.append(record)
        logger.info(f"[MOCK_X] Simulated publish: {mock_id} -> '{trimmed_text[:50]}...'")
        return record


# Backwards compatibility wrapper
class TwitterClient:
    """Legacy Twitter client retained for backwards compatibility."""

    def __init__(self):
        self.publisher = XPublisher()

    async def post(self, text: str, media_ids: list[str] | None = None) -> dict[str, Any]:
        result = await self.publisher.publish_post(text)
        if not result["success"]:
            raise RuntimeError(result.get("error", "Failed to post"))
        return {"id": result["post_id"], "text": text}
