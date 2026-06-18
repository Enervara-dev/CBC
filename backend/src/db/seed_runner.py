"""
Seed runner — populate the reference-range knowledge base in the configured DB.

Usage (from ``backend/``, with the DB migrated)::

    python -m db.seed_runner

Uses the sync database URL (``ALEMBIC_DATABASE_URL`` / ``DATABASE_URL``).
"""

from __future__ import annotations

from db.seeds import seed_all
from db.session import get_sync_session


def main() -> None:
    with get_sync_session() as session:
        inserted = seed_all(session)
    print(f"Reference ranges seeded. New rows inserted: {inserted} (existing rows updated).")


if __name__ == "__main__":
    main()
