"""
Tests for ``db.session`` URL handling.

One ``DATABASE_URL`` drives both drivers — asyncpg for the app, psycopg2 for
Alembic and seeding — so the translation between them is load-bearing. These
lock in the behaviour after the move from Supabase to AWS Aurora:

  - AWS-managed hosts (``*.rds.amazonaws.com``) get TLS by default on *both*
    drivers; an explicit ``sslmode`` always wins.
  - Local hosts stay plaintext, so ``localhost`` development keeps working.
  - The old Supabase special-casing is gone; a transaction pooler is opted into
    explicitly with ``statement_cache_size`` instead of being guessed from a port.
"""

from urllib.parse import parse_qs, urlsplit

import pytest

from db.session import make_async_url, make_sync_url

AURORA = "postgresql://cbc_app:s3cret@enervara-aurora-pg17.cluster-abc.ap-south-1.rds.amazonaws.com:5432/cbc"


def _query(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


class TestAsyncUrl:
    def test_aurora_host_requires_tls_by_default(self):
        url, args = make_async_url(AURORA)
        assert url.startswith("postgresql+asyncpg://")
        assert args["ssl"] == "require"

    def test_libpq_params_are_translated_not_passed_to_asyncpg(self):
        """asyncpg rejects sslmode/channel_binding as URL params."""
        url, args = make_async_url(AURORA + "?sslmode=verify-full&channel_binding=require")
        assert "sslmode" not in _query(url) and "channel_binding" not in _query(url)
        assert args["ssl"] == "verify-full"

    def test_explicit_disable_wins_even_for_aws(self):
        _, args = make_async_url(AURORA + "?sslmode=disable")
        assert "ssl" not in args

    def test_local_host_stays_plaintext(self):
        _, args = make_async_url("postgresql://postgres:pw@localhost:5432/enervera")
        assert "ssl" not in args

    def test_statement_cache_is_an_explicit_opt_in(self):
        url, args = make_async_url(AURORA + "?statement_cache_size=0")
        assert args["statement_cache_size"] == 0
        assert "statement_cache_size" not in _query(url)

    def test_direct_aurora_keeps_prepared_statements(self):
        _, args = make_async_url(AURORA)
        assert "statement_cache_size" not in args

    def test_supabase_is_no_longer_special_cased(self):
        """Regression guard for the migration: no host or port is guessed at."""
        _, args = make_async_url("postgresql://u:p@aws-0-x.pooler.supabase.com:6543/postgres")
        assert "statement_cache_size" not in args
        assert "ssl" not in args

    def test_encoded_password_survives(self):
        url, _ = make_async_url(AURORA.replace("s3cret", "p%40ss%3Aword"))
        assert "p%40ss%3Aword@" in url


class TestSyncUrl:
    def test_aurora_host_gets_sslmode_require(self):
        url = make_sync_url(AURORA)
        assert url.startswith("postgresql+psycopg2://")
        assert _query(url)["sslmode"] == "require"

    @pytest.mark.parametrize("mode", ["verify-full", "disable", "prefer"])
    def test_explicit_sslmode_is_preserved(self, mode):
        assert _query(make_sync_url(AURORA + f"?sslmode={mode}"))["sslmode"] == mode

    def test_local_host_is_left_alone(self):
        url = make_sync_url("postgresql://postgres:pw@localhost:5432/enervera")
        assert url == "postgresql+psycopg2://postgres:pw@localhost:5432/enervera"

    def test_encoded_password_survives(self):
        assert "p%40ss%3Aword@" in make_sync_url(AURORA.replace("s3cret", "p%40ss%3Aword"))


class TestConnectionPool:
    """A quiet night left dead pooled connections; the next request lost every lookup."""

    def test_app_engine_checks_connections_before_use(self, monkeypatch):
        from types import SimpleNamespace
        import db.session as session

        monkeypatch.setattr(session, "get_settings", lambda: SimpleNamespace(
            database_url="postgresql://u:p@db.cluster-x.ap-south-1.rds.amazonaws.com:5432/cbc",
            alembic_database_url=None,
        ))
        engine = session.get_async_engine()
        try:
            assert engine.sync_engine.pool._pre_ping is True
            assert engine.sync_engine.pool._recycle == 1800
        finally:
            engine.sync_engine.dispose()
