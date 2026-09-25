"""
Application settings, loaded from the project-root ``.env`` (``CBC/.env``).

Database (AWS Aurora PostgreSQL — ``cbc`` database on ``enervara-aurora-pg17``):
  - ``DATABASE_URL``         the connection string for the whole app. The async
                             (asyncpg) and sync (psycopg2) drivers are derived from
                             it in ``db.session`` — one URL is enough.
  - ``ALEMBIC_DATABASE_URL`` optional explicit override for the sync (migrations/
                             seeds) URL. Leave unset to derive it from DATABASE_URL.

Secrets stay in the environment — nothing here logs or prints the URLs.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# CBC/.env  (this file is backend/src/db/config.py → parents[3] == CBC/)
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    """Environment-backed settings."""

    database_url: str = ""           # Aurora PostgreSQL connection string (drivers derived in db.session)
    alembic_database_url: str = ""   # optional explicit sync URL override for Alembic / seeding
    # Deployment environment: prefer ENVIRONMENT, accept legacy APP_ENV.
    app_env: str = Field(
        default="development",
        validation_alias=AliasChoices("ENVIRONMENT", "APP_ENV"),
    )
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
