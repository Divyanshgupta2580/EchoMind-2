"""
Application settings using Pydantic Settings.

Loads configuration from environment variables and .env file with resilient defaults:
- Deployed EchoMind Backend API base URL.
- OpenRouter API credentials for LLM inference & live web search.
- SQLite database storage path.
- ~35-Minute discovery interval with ±5-minute jitter & 2-Hour publishing window duration.
- Configurable minimum news score threshold (default: 75.0).
- Multi-agent cap (MAX_AGENTS = 5).
"""

import os
from dotenv import find_dotenv, load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Explicitly load .env into os.environ without overwriting process environment variables
load_dotenv(find_dotenv() or ".env", override=False)


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

    # Database & Storage configuration
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///agent_memory.db")
    agent_db_path: str = os.getenv("AGENT_DB_PATH", "agent_memory.db")

    # Publishing and cycle configuration
    # Discovery runs every 30-45 minutes with ±5-min jitter (300s); Publishing Window matches discovery interval (45 min).
    discovery_interval_minutes: int = int(os.getenv("DISCOVERY_INTERVAL_MINUTES", os.getenv("AGENT_INTERVAL_MINUTES", "45")))
    discovery_jitter_seconds: int = int(os.getenv("DISCOVERY_JITTER_SECONDS", "300"))  # ±5 minutes
    publish_window_minutes: int = int(os.getenv("PUBLISH_WINDOW_MINUTES", "45"))
    min_news_score: float = float(os.getenv("MIN_NEWS_SCORE", "75.0"))

    # Optional Admin API Secret for manual debug endpoints
    admin_api_key: str = os.getenv("ADMIN_API_KEY", "")


# Global settings instance
settings = Settings()
