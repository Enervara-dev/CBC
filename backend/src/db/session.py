"""
Database engine / session factories (Supabase PostgreSQL).

A single ``DATABASE_URL`` (from the environment / project ``.env``) drives both:
  - Sync  (``get_sync_session``)  → Alembic migrations and seeding (psycopg2).
  - Async (``get_async_sessionmaker``) → the running app (asyncpg, AsyncSession).

The driver is normalised per use, so the same ``DATABASE_URL`` works for both —
e.g. Supabase's ``postgresql://postgres:...@db.<ref>.supabase.co:5432/postgres``.
``ALEMBIC_DATABASE_URL`` may still be set to override the sync URL explicitly.
No credentials are hard-coded or logged here.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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


def _with_driver(url: str, driver: str) -> str:
    """Force the SQLAlchemy driver on a Postgres URL (``postgresql+<driver>://``)."""
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    base = scheme.split("+", 1)[0]
    if base in ("postgres", "postgresql"):
        base = "postgresql"
    return f"{base}+{driver}://{rest}"


_SSLMODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")


def _async_ssl_arg(netloc: str, sslmode: str) -> Optional[str]:
    """
    Return the libpq ``sslmode`` **string** to hand asyncpg (not an SSLContext).

    asyncpg understands the sslmode strings directly and applies libpq semantics
    itself. Passing the string (rather than an ``ssl.SSLContext`` object) is what
    keeps this robust to ``truststore.inject_into_ssl()`` — which the Gemini client
    calls and which globally monkeypatches ``ssl.SSLContext``, breaking asyncpg's
    ``isinstance`` check on a raw context passed for the DB connection.

    - ``disable``                       → no TLS (``None``).
    - explicit sslmode                  → passed through verbatim.
    - none set, but Supabase host       → default to ``require`` (Supabase needs TLS).
    """
    if sslmode == "disable":
        return None
    if sslmode in _SSLMODES:
        return sslmode
    if "supabase." in netloc or "pooler.supabase" in netloc:
        return "require"
    return None


def make_async_url(url: str) -> Tuple[str, Dict[str, Any]]:
    """
    Build the asyncpg URL + connect args from ``url``.

    asyncpg does not understand libpq query params (``sslmode``/``channel_binding``),
    so they are stripped from the URL and translated into connect args: the ``ssl``
    sslmode string (see :func:`_async_ssl_arg`) and — for Supabase's transaction
    pooler (port 6543) — ``statement_cache_size=0`` (pgbouncer transaction pooling is
    incompatible with server-side prepared statements).
    """
    async_url = _with_driver(url, "asyncpg")
    parts = urlsplit(async_url)
    query = dict(parse_qsl(parts.query))
    sslmode = (query.get("sslmode") or "").lower()
    for libpq_only in ("sslmode", "channel_binding"):
        query.pop(libpq_only, None)
    cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    connect_args: Dict[str, Any] = {}
    ssl_arg = _async_ssl_arg(parts.netloc, sslmode)
    if ssl_arg is not None:
        connect_args["ssl"] = ssl_arg
    if ":6543" in parts.netloc or "pooler.supabase" in parts.netloc:
        connect_args["statement_cache_size"] = 0
    return cleaned, connect_args


def make_sync_url(url: str) -> str:
    """Build the psycopg2 (sync) URL from ``url`` (libpq params kept as-is)."""
    return _with_driver(url, "psycopg2")


def sync_database_url() -> str:
    """Sync SQLAlchemy URL for migrations/seeds (explicit Alembic URL, else the app URL)."""
    settings = get_settings()
    if settings.alembic_database_url:
        return make_sync_url(settings.alembic_database_url)
    if settings.database_url:
        return make_sync_url(settings.database_url)
    raise RuntimeError(
        "No database URL configured. Set DATABASE_URL (or ALEMBIC_DATABASE_URL) "
        "in the project .env."
    )


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
    """Create the async engine for the app (asyncpg + Supabase TLS)."""
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured in the project .env.")
    url, connect_args = make_async_url(settings.database_url)
    return create_async_engine(url, future=True, connect_args=connect_args)


def get_async_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Async session factory for the app (e.g. FastAPI dependencies)."""
    return async_sessionmaker(get_async_engine(), expire_on_commit=False, class_=AsyncSession)
