"""Runtime configuration (PRD-N-002 / LLD §0 endpoints).

Reads the self-hosted LLM/embedding endpoints, the database URL and the SMTP
relay from the environment or a local ``.env`` file. Secrets never live in code
(see ``.env.example`` for the shape).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Framework-wide settings, populated from env / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+psycopg://agent:change-me@localhost:5433/jyotech_v1"

    # LLM (chat) endpoint — OpenAI-compatible, self-hosted
    llm_base_url: str = "http://llm.internal:8000/v1"
    llm_model: str = ""

    # Embedding endpoint — OpenAI-compatible, self-hosted
    embed_base_url: str = "http://embed.internal:8001/v1"
    embed_model: str = "bge-m3"

    # Outbound email relay (handoff dispatch, LLD-HO-04)
    smtp_url: str = "smtp://user:pass@smtp.internal:587"


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance."""
    return Settings()
