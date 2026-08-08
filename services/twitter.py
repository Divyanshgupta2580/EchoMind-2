"""
Official X / Twitter Publishing Implementation.

Provides:
1. IXPublisher abstraction interface adherence.
2. XPublisher: Tweepy API v2 integration with OAuth 1.0a / Bearer authentication,
   persistent SQLite idempotency tracking, 280-character validation, and bounded retry policy.
3. Safe duplicate reconciliation: Distinguishes genuine duplicate-content errors from generic
   403 Forbidden / authentication / tier permission failures, preventing false success classifications.
4. MockXPublisher: In-memory mock publisher for automated testing and offline verification.
"""

import asyncio
import hashlib
import logging
import os
import re
from typing import Any

from config.settings import settings
from services.publisher_interface import IXPublisher

logger = logging.getLogger(__name__)

# Maximum allowed characters for standard X / Twitter posts
MAX_X_POST_LENGTH = 280


def is_valid_x_post_id(post_id: str | None) -> bool:
    """
    Validate that post_id is a genuine numeric X/Twitter Snowflake ID (1-20 digits).
    Rejects None, empty strings, and synthetic strings ('x-mock-*', 'x-confirmed-*', 'pub-*', 'agent-*', etc.).
    """
    if not post_id or not isinstance(post_id, str):
        return False
    clean_id = post_id.strip()
    if clean_id.startswith(("x-mock", "x-confirmed", "pub-", "agent-", "mock-", "confirmed-")):
        return False
    return bool(re.match(r"^\d{1,20}$", clean_id))


def is_explicit_duplicate_error(exception: Exception | None) -> bool:
    """
    Check if a Tweepy/X exception explicitly indicates a duplicate-content error.
    Must distinguish between genuine duplicate-content errors (code 187, 'duplicate content')
    and generic 403 Forbidden errors (e.g. read-only permissions, missing auth scopes, tier limits).
    """
    if exception is None:
        return False

    err_str = str(exception).lower()

    # Explicit Tweepy API error code 187 ("Status is a duplicate.")
    api_codes = getattr(exception, "api_codes", None)
    if api_codes and 187 in api_codes:
        return True

    # Check Tweepy response payload text if available
    response = getattr(exception, "response", None)
    if response is not None:
        try:
            if hasattr(response, "text"):
                resp_text = response.text.lower()
                if "duplicate content" in resp_text or "status is a duplicate" in resp_text:
                    return True
        except Exception:
            pass

    # Check for explicit duplicate content wording in error message
    duplicate_indicators = [
        "duplicate content",
        "status is a duplicate",
        "tweet with duplicate content",
        "duplicate_content"
    ]
    for indicator in duplicate_indicators:
        if indicator in err_str:
            return True

    return False


class XPublisher(IXPublisher):
    """
    Production publisher for X/Twitter using official API v2 via Tweepy.
    Implements character limit validation, persistent idempotency, and bounded retries.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        access_token: str | None = None,
        access_token_secret: str | None = None,
        bearer_token: str | None = None,
        max_retries: int = 3,
        memory_store: Any | None = None
    ):
        self.api_key = api_key or settings.x_api_key
        self.api_secret = api_secret or settings.x_api_secret
        self.access_token = access_token or settings.x_access_token
        self.access_token_secret = access_token_secret or settings.x_access_token_secret
        self.bearer_token = bearer_token or settings.x_bearer_token
        self.max_retries = max_retries
        self.memory_store = memory_store
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

    def get_authenticated_username(self) -> str | None:
        """
        Safely check authenticated X account using GET /2/users/me without exposing credentials.
        Returns username string (e.g. 'RASBASRYPI') or None if unauthorized/unavailable.
        """
        res = self.verify_authenticated_account()
        return res.get("handle")

    def verify_authenticated_account(self) -> dict[str, Any]:
        """
        Verify authenticated X account using client.get_me() (GET /2/users/me).
        If EXPECTED_X_HANDLE is configured, validates that authenticated handle matches expected handle.
        Returns dict with success: bool, handle: str | None, error: str | None.
        """
        expected_handle = (
            getattr(settings, "x_expected_handle", None)
            or os.getenv("EXPECTED_X_HANDLE", "")
            or os.getenv("X_EXPECTED_HANDLE", "")
        ).lstrip("@").strip()

        try:
            client = self._get_client()
            user_res = client.get_me()
            if not user_res or not getattr(user_res, "data", None):
                err_msg = "X API get_me() returned response without user data."
                logger.error(f"[X_PUBLISHER] Account verification failed: {err_msg}")
                return {"success": False, "handle": None, "error": err_msg}

            username = None
            if hasattr(user_res.data, "username"):
                username = getattr(user_res.data, "username")
            elif isinstance(user_res.data, dict):
                username = user_res.data.get("username")

            if not username:
                err_msg = "X API get_me() response data missing username field."
                logger.error(f"[X_PUBLISHER] Account verification failed: {err_msg}")
                return {"success": False, "handle": None, "error": err_msg}

            actual_username = str(username).lstrip("@").strip()
            if expected_handle:
                if actual_username.lower() != expected_handle.lower():
                    err_msg = f"Authenticated X account handle @{actual_username} does not match expected handle @{expected_handle}."
                    logger.error(f"[X_PUBLISHER] Account verification failed: {err_msg}")
                    return {"success": False, "handle": actual_username, "error": err_msg}
                logger.info(f"[X_PUBLISHER] Authenticated X account handle verified: @{actual_username} matches expected @{expected_handle}")
            else:
                logger.info(f"[X_PUBLISHER] EXPECTED_X_HANDLE not set; authenticated handle is @{actual_username}")

            return {"success": True, "handle": actual_username, "error": None}

        except Exception as e:
            err_msg = f"X API get_me() failed: {e}"
            logger.error(f"[X_PUBLISHER] Account verification failed: {err_msg}")
            return {"success": False, "handle": None, "error": err_msg}

    def _verify_tweet_exists(self, client: Any, tweet_id: str) -> bool:
        """
        Perform independent verification (GET /2/tweets/{id}) that the tweet actually exists on X.
        """
        try:
            tweet_res = client.get_tweet(id=tweet_id)
            if not tweet_res or not getattr(tweet_res, "data", None):
                logger.error(f"[X_PUBLISHER] Independent tweet verification failed: GET /2/tweets/{tweet_id} returned no data.")
                return False

            returned_id = None
            if hasattr(tweet_res.data, "id"):
                returned_id = str(getattr(tweet_res.data, "id")).strip()
            elif isinstance(tweet_res.data, dict):
                returned_id = str(tweet_res.data.get("id", "")).strip()

            if returned_id == str(tweet_id).strip():
                logger.info(f"[X_PUBLISHER] Independent tweet existence verified on X for ID: {tweet_id}")
                return True
            else:
                logger.error(f"[X_PUBLISHER] Tweet existence check ID mismatch: created {tweet_id}, returned {returned_id}")
                return False
        except Exception as e:
            logger.error(f"[X_PUBLISHER] Independent tweet existence check failed for ID {tweet_id}: {e}")
            return False

    async def publish_post(
        self,
        text: str,
        metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Publish post to X with character validation, persistent idempotency, and safe retries.
        Never fabricates synthetic post IDs.
        """
        metadata = metadata or {}
        trimmed_text = text.strip()

        # 1. Credentials Validation (Fail closed if credentials missing)
        if not (self.api_key and self.api_secret and self.access_token and self.access_token_secret):
            logger.error("[X_PUBLISHER] X API credentials not fully configured in environment. Failing closed.")
            return {
                "success": False,
                "status": "FAILED",
                "post_id": None,
                "text": trimmed_text,
                "error": "X API credentials not configured in environment (MISSING_CREDENTIALS)"
            }

        # 2. Account Authentication Verification (Fail closed if get_me fails or handle mismatches)
        auth_check = self.verify_authenticated_account()
        if not auth_check["success"]:
            logger.error(f"[X_PUBLISHER] Account verification failed: {auth_check['error']}. Aborting publication.")
            return {
                "success": False,
                "status": "FAILED",
                "post_id": None,
                "text": trimmed_text,
                "error": f"Account verification failed: {auth_check['error']}"
            }

        # 3. Character Limit Validation (Must be <= 280 characters)
        if len(trimmed_text) > MAX_X_POST_LENGTH:
            logger.error(f"[X_PUBLISHER] Post exceeds {MAX_X_POST_LENGTH} characters ({len(trimmed_text)} chars).")
            return {
                "success": False,
                "status": "FAILED",
                "post_id": None,
                "text": trimmed_text,
                "error": f"Character limit exceeded: {len(trimmed_text)} > {MAX_X_POST_LENGTH}"
            }

        # 4. Idempotency Check (In-memory + Persistent SQLite)
        idempotency_key = metadata.get("idempotency_key") or hashlib.sha256(trimmed_text.encode("utf-8")).hexdigest()[:16]

        # Check in-memory fast cache
        if idempotency_key in self._published_idempotency_keys:
            existing_post_id = self._published_idempotency_keys[idempotency_key]
            logger.info(f"[X_PUBLISHER] Post already published (In-memory Idempotency: {idempotency_key} -> {existing_post_id})")
            return {
                "success": True,
                "status": "PUBLISHED",
                "post_id": existing_post_id,
                "text": trimmed_text,
                "error": None,
                "is_duplicate": True
            }

        # Check persistent SQLite store for restart durability
        if self.memory_store and hasattr(self.memory_store, "get_x_publication_record"):
            rec = self.memory_store.get_x_publication_record(idempotency_key)
            if rec and rec.get("post_id") and not str(rec.get("post_id")).startswith("x-confirmed"):
                logger.info(f"[X_PUBLISHER] Post already recorded in persistent SQLite ({idempotency_key} -> {rec['post_id']})")
                self._published_idempotency_keys[idempotency_key] = rec["post_id"]
                return {
                    "success": True,
                    "status": "PUBLISHED",
                    "post_id": rec["post_id"],
                    "text": trimmed_text,
                    "error": None,
                    "is_duplicate": True
                }

        # 5. Execution with Bounded Retries and Genuine Response Verification
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                client = self._get_client()
                logger.info(f"[X_PUBLISHER] Publishing to X (attempt {attempt}/{self.max_retries}): {trimmed_text[:60]}...")

                # Execute Tweepy create_tweet
                response = client.create_tweet(text=trimmed_text)
                if not response or not getattr(response, "data", None) or "id" not in response.data:
                    raise ValueError(f"X API returned response without valid post ID: {response}")

                post_id = str(response.data["id"]).strip()

                # Perform independent post-publication GET /2/tweets/{id} verification
                if not self._verify_tweet_exists(client, post_id):
                    logger.error(f"[X_PUBLISHER] Tweet created (ID: {post_id}), but independent GET verification failed. Refusing to claim PUBLISHED.")
                    return {
                        "success": False,
                        "status": "FAILED",
                        "post_id": None,
                        "text": trimmed_text,
                        "error": f"Independent tweet existence verification failed for ID: {post_id}"
                    }

                self._published_idempotency_keys[idempotency_key] = post_id
                if self.memory_store and hasattr(self.memory_store, "save_x_publication_record"):
                    self.memory_store.save_x_publication_record(
                        idempotency_key=idempotency_key,
                        post_id=post_id,
                        text=trimmed_text,
                        agent_id=metadata.get("agent_id", "unknown"),
                        window_id=metadata.get("window_id", "unknown")
                    )

                logger.info(f"[X_PUBLISHER] Successfully published to X and independently verified! Post ID: {post_id}")
                return {
                    "success": True,
                    "status": "PUBLISHED",
                    "post_id": post_id,
                    "text": trimmed_text,
                    "error": None
                }

            except Exception as e:
                last_error = e
                err_code = getattr(e, "api_codes", None) or getattr(e, "status_code", None)
                err_str = str(e).lower()
                status_code = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
                logger.warning(f"[X_PUBLISHER] Attempt {attempt}/{self.max_retries} failed (Code: {err_code or status_code}): {e}")

                # 1. Structured duplicate reconciliation: Only reconcile if explicitly duplicate content
                # AND a genuine post ID was previously recorded for this idempotency key.
                if is_explicit_duplicate_error(e):
                    recorded_post_id = self._published_idempotency_keys.get(idempotency_key)
                    if not recorded_post_id and self.memory_store and hasattr(self.memory_store, "get_x_publication_record"):
                        rec = self.memory_store.get_x_publication_record(idempotency_key)
                        if rec and rec.get("post_id") and not str(rec.get("post_id")).startswith("x-confirmed"):
                            recorded_post_id = rec["post_id"]

                    if recorded_post_id:
                        logger.info(f"[X_PUBLISHER] Reconciled duplicate with verified X post ID: {recorded_post_id}")
                        return {
                            "success": True,
                            "status": "PUBLISHED",
                            "post_id": recorded_post_id,
                            "text": trimmed_text,
                            "error": None,
                            "is_duplicate": True,
                            "reconciled": True
                        }
                    else:
                        logger.error(f"[X_PUBLISHER] X reported duplicate content, but no verified post ID exists. Refusing to fabricate post ID.")
                        return {
                            "success": False,
                            "status": "FAILED",
                            "post_id": None,
                            "text": trimmed_text,
                            "error": f"X duplicate error without verified post ID: {e}"
                        }

                # 2. Deterministic Permission / Authentication Failures (HTTP 401 / 403 without duplicate indicator)
                is_permission_error = (
                    status_code in (401, 403)
                    or "401" in err_str
                    or "403" in err_str
                    or "forbidden" in err_str
                    or "unauthorized" in err_str
                    or "not allowed to create" in err_str
                )
                if is_permission_error:
                    logger.error(f"[X_PUBLISHER] Deterministic permission/auth failure ({err_code or status_code}): {e}. Aborting without retry.")
                    return {
                        "success": False,
                        "status": "FAILED",
                        "post_id": None,
                        "text": trimmed_text,
                        "error": f"X API permission/auth failure: {e}"
                    }

                # 3. Retryable Errors (HTTP 429 Rate Limit, 5xx Server Errors, Connection/Timeout drops)
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
    Guarantees zero external network requests and safe simulation.
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

        if self.should_fail:
            return {
                "success": False,
                "status": "FAILED",
                "post_id": None,
                "text": trimmed_text,
                "error": "Simulated mock network failure"
            }

        mock_post_id = f"1999{len(self.published_posts) + 1:015d}"
        self._idempotency_map[idempotency_key] = mock_post_id
        record = {
            "post_id": mock_post_id,
            "text": trimmed_text,
            "metadata": metadata
        }
        self.published_posts.append(record)
        return {
            "success": True,
            "status": "PUBLISHED",
            "post_id": mock_post_id,
            "text": trimmed_text,
            "error": None
        }
