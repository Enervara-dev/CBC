"""
Application settings, loaded from the project-root ``.env`` (``CBC/.env``).

Two database URLs are used (see the .env):
  - ``DATABASE_URL``         async URL for the app   (e.g. postgresql+asyncpg://…)
  - ``ALEMBIC_DATABASE_URL`` sync URL for migrations & seeds (e.g. postgresql+psycopg2://…)

Secrets stay in the environment — nothing here logs or prints the URLs.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# CBC/.env  (this file is backend/src/db/config.py → parents[3] == CBC/)
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    """Environment-backed settings."""

    database_url: str = ""           # async driver URL for the running app
    alembic_database_url: str = ""   # sync driver URL for Alembic / seeding
    app_env: str = "development"
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
