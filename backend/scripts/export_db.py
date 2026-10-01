"""
Export every row of the live PostgreSQL database to a single JSON file.

Usage (from ``backend/``)::

    python -m scripts.export_db                 # -> ../db_export.json
    python -m scripts.export_db out/dump.json   # explicit destination

Introspects the live schema rather than the ORM models, so tables that exist in
the database but are not (yet) modelled in ``db.models`` are exported too.
Connection details come from ``DATABASE_URL`` / ``ALEMBIC_DATABASE_URL`` in the
project ``.env`` — nothing is hard-coded and the URL is never printed.
"""

from __future__ import annotations

import datetime
import decimal
import json
import sys
import uuid
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import create_engine, inspect, text  # noqa: E402

from db.session import sync_database_url  # noqa: E402

# Schemas owned by PostgreSQL/Supabase internals — never application data.
_SKIP_SCHEMAS = {"information_schema", "pg_catalog", "pg_toast"}


def _json_default(obj: Any) -> Any:
    """Serialize the Postgres types json doesn't handle natively."""
    if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
        return obj.isoformat()
    if isinstance(obj, datetime.timedelta):
        return obj.total_seconds()
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (bytes, memoryview)):
        return bytes(obj).hex()
    return str(obj)


def export(destination: Path) -> dict[str, Any]:
    """Dump all rows of every user table, keyed by schema then table name."""
    engine = create_engine(sync_database_url(), future=True)
    inspector = inspect(engine)
    payload: dict[str, Any] = {"source": "postgresql", "schemas": {}}

    try:
        with engine.connect() as conn:
            for schema in inspector.get_schema_names():
                if schema in _SKIP_SCHEMAS or schema.startswith("pg_"):
                    continue
                tables = inspector.get_table_names(schema=schema)
                if not tables:
                    continue
                payload["schemas"][schema] = {}
                for table in tables:
                    columns = [c["name"] for c in inspector.get_columns(table, schema=schema)]
                    rows = conn.execute(
                        text(f'SELECT * FROM "{schema}"."{table}"')
                    ).mappings().all()
                    payload["schemas"][schema][table] = {
                        "columns": columns,
                        "row_count": len(rows),
                        "rows": [dict(r) for r in rows],
                    }
                    print(f"  {schema}.{table}: {len(rows)} rows")
    finally:
        engine.dispose()

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=_json_default, ensure_ascii=False)
    return payload


def main() -> None:
    default = Path(__file__).resolve().parents[2] / "db_export.json"
    destination = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else default
    export(destination)
    print(f"Wrote {destination}")


if __name__ == "__main__":
    main()
