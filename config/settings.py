"""
Application settings using Pydantic Settings.

Loads configuration from environment variables and .env file with resilient defaults.
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

    # OpenRouter API (used for LLM inference & web search)
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")

    # Twitter API credentials (optional / legacy)
    twitter_api_key: str = os.getenv("TWITTER_API_KEY", "")
    twitter_api_secret: str = os.getenv("TWITTER_API_SECRET", "")
    twitter_access_token: str = os.getenv("TWITTER_ACCESS_TOKEN", "")
    twitter_access_secret: str = os.getenv("TWITTER_ACCESS_SECRET", "")
    twitter_bearer_token: str = os.getenv("TWITTER_BEARER_TOKEN", "")

    # Database & Storage configuration
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///agent_memory.db")
    agent_db_path: str = os.getenv("AGENT_DB_PATH", "agent_memory.db")

    # Publishing and cycle configuration
    agent_interval_minutes: int = int(os.getenv("AGENT_INTERVAL_MINUTES", "2"))
    post_interval_minutes: int = 30
    mentions_interval_minutes: int = 20
    enable_image_generation: bool = False
    use_unified_agent: bool = True
    allow_mentions: bool = False


# Global settings instance
settings = Settings()
