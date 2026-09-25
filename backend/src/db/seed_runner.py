"""
Seed runner — populate the reference-range knowledge base in the configured DB.

Seeds every registered panel (CBC, LFT, Lipid, …) — see ``domains/registry.py``.

Usage (from ``backend/``, with the DB migrated)::

    python -m db.seed_runner

Uses the sync database URL (``ALEMBIC_DATABASE_URL`` / ``DATABASE_URL``).
"""

from __future__ import annotations

from db.seeds import seed_all, seed_counts
from db.session import get_sync_session


def main() -> None:
    counts = seed_counts()
    with get_sync_session() as session:
        inserted = seed_all(session)
    breakdown = ", ".join(f"{key}={count}" for key, count in sorted(counts.items()))
    print(
        f"Reference ranges seeded for {len(counts)} panel(s) [{breakdown}]. "
        f"Total rows: {sum(counts.values())}; new rows inserted: {inserted} "
        "(existing rows updated)."
    )


if __name__ == "__main__":
    main()
