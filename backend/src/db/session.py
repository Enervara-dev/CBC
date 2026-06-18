"""
Database engine / session factories.

  - Sync  (``get_sync_session``)  → Alembic migrations and seeding.
  - Async (``get_async_sessionmaker``) → the running app (AsyncSession lookups).

Sync work uses ``ALEMBIC_DATABASE_URL`` (falling back to ``DATABASE_URL``); the
async app uses ``DATABASE_URL``.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from db.config import get_settings


def sync_database_url() -> str:
    """Sync SQLAlchemy URL for migrations/seeds (Alembic URL, else the app URL)."""
    settings = get_settings()
    url = settings.alembic_database_url or settings.database_url
    if not url:
        raise RuntimeError(
            "No sync database URL configured. Set ALEMBIC_DATABASE_URL (or "
            "DATABASE_URL) in the project .env."
        )
    return url


def get_sync_engine() -> Engine:
    """Create a sync engine for migrations/seeding."""
    return create_engine(sync_database_url(), future=True)


@contextmanager
def get_sync_session() -> Iterator[Session]:
    """Context-managed sync session (engine disposed on exit)."""
    engine = get_sync_engine()
    factory = sessionmaker(bind=engine, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def get_async_engine() -> AsyncEngine:
    """Create the async engine for the app."""
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured in the project .env.")
    return create_async_engine(settings.database_url, future=True)


def get_async_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Async session factory for the app (e.g. FastAPI dependencies)."""
    return async_sessionmaker(get_async_engine(), expire_on_commit=False, class_=AsyncSession)
