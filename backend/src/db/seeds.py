"""
Reference-range seeding — promotes per-panel knowledge into the database.

The actual range *data* lives with its domain (``domains.cbc.reference_ranges``);
this module is the generic, panel-agnostic loader: ``seed_all`` upserts whatever
rows the domain provides. It is idempotent — safe to re-run.

To seed a different specialty, point ``reference_range_rows`` at that domain (or
extend this to iterate the domain registry).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import ReferenceRange
from domains.cbc.reference_ranges import reference_range_rows


def seed_all(session: Session) -> int:
    """
    Idempotently upsert all reference ranges.

    Returns
    -------
    int
        The number of *new* rows inserted (existing rows are updated in place).
    """
    inserted = 0
    for data in reference_range_rows():
        existing = session.execute(
            select(ReferenceRange).filter_by(
                biomarker_id=data["biomarker_id"],
                gender=data["gender"],
                age_min=data["age_min"],
                age_max=data["age_max"],
                lab_source=data["lab_source"],
                condition=data["condition"],
            )
        ).scalar_one_or_none()

        if existing is None:
            session.add(ReferenceRange(**data))
            inserted += 1
        else:
            for key, value in data.items():
                setattr(existing, key, value)

    session.commit()
    return inserted
