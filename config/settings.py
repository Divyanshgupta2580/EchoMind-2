"""
Application settings using Pydantic Settings.

Loads configuration from environment variables and .env file with resilient defaults:
- Deployed EchoMind Backend API base URL (https://echomind-ltwo.onrender.com).
- OpenRouter API credentials for LLM inference & web search.
- Official X/Twitter API credentials for news publishing.
- SQLite database storage path.
- 5-Minute continuous discovery interval & 2-Hour publishing window duration.
- Configurable minimum news score threshold (default: 75.0).
- Multi-agent cap (MAX_AGENTS = 5).
"""

import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # Deployed Backend API Base URL
    # Production default: https://echomind-ltwo.onrender.com
    # Local development override: http://localhost:8080 or http://127.0.0.1:8080
    api_base_url: str = os.getenv(
        "ECHOMIND_API_BASE_URL",
        os.getenv("API_BASE_URL", "https://echomind-ltwo.onrender.com")
    ).rstrip("/")

    # Multi-agent capacity limit (default: 5)
    max_agents: int = int(os.getenv("MAX_AGENTS", "5"))

    # OpenRouter API (used for LLM inference & web search)
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")

    # Official X / Twitter API credentials
    x_api_key: str = os.getenv("X_API_KEY", os.getenv("TWITTER_API_KEY", ""))
    x_api_secret: str = os.getenv("X_API_SECRET", os.getenv("TWITTER_API_SECRET", ""))
    x_access_token: str = os.getenv("X_ACCESS_TOKEN", os.getenv("TWITTER_ACCESS_TOKEN", ""))
    x_access_token_secret: str = os.getenv("X_ACCESS_TOKEN_SECRET", os.getenv("TWITTER_ACCESS_SECRET", ""))
    x_bearer_token: str = os.getenv("X_BEARER_TOKEN", os.getenv("TWITTER_BEARER_TOKEN", ""))
    x_expected_handle: str = os.getenv("EXPECTED_X_HANDLE", os.getenv("X_EXPECTED_HANDLE", ""))

    # Backwards-compatible aliases
    twitter_api_key: str = os.getenv("TWITTER_API_KEY", os.getenv("X_API_KEY", ""))
    twitter_api_secret: str = os.getenv("TWITTER_API_SECRET", os.getenv("X_API_SECRET", ""))
    twitter_access_token: str = os.getenv("TWITTER_ACCESS_TOKEN", os.getenv("X_ACCESS_TOKEN", ""))
    twitter_access_secret: str = os.getenv("TWITTER_ACCESS_SECRET", os.getenv("X_ACCESS_TOKEN_SECRET", ""))
    twitter_bearer_token: str = os.getenv("TWITTER_BEARER_TOKEN", os.getenv("X_BEARER_TOKEN", ""))

    # Database & Storage configuration
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///agent_memory.db")
    agent_db_path: str = os.getenv("AGENT_DB_PATH", "agent_memory.db")

    # Publishing and cycle configuration
    # Discovery runs every ~5 minutes; Publishing Window lasts 120 minutes (2 hours).
    discovery_interval_minutes: int = int(os.getenv("DISCOVERY_INTERVAL_MINUTES", os.getenv("AGENT_INTERVAL_MINUTES", "5")))
    publish_window_minutes: int = int(os.getenv("PUBLISH_WINDOW_MINUTES", "120"))
    min_news_score: float = float(os.getenv("MIN_NEWS_SCORE", "75.0"))

    # Optional Admin API Secret for manual debug endpoints
    admin_api_key: str = os.getenv("ADMIN_API_KEY", "")

    # Legacy flags preserved for backwards compatibility
    agent_interval_minutes: int = int(os.getenv("AGENT_INTERVAL_MINUTES", "5"))
    post_interval_minutes: int = 120
    mentions_interval_minutes: int = 20
    enable_image_generation: bool = False
    use_unified_agent: bool = True
    allow_mentions: bool = False


# Global settings instance
settings = Settings()
