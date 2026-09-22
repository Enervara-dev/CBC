"""
Reference-range seeding — promotes per-panel knowledge into the database.

The actual range *data* lives with its domain (``domains/<panel>/reference_ranges.py``);
this module is the generic, panel-agnostic loader: ``seed_all`` upserts the rows
of **every registered domain** (CBC, LFT, Lipid, …). It is idempotent — safe to
re-run — and adding a panel needs no edit here, only a registry entry.

Rows are matched on the same dimensions as the table's unique constraint
(biomarker + gender + age band + lab + condition), so re-seeding updates values
in place rather than duplicating them.
"""

from __future__ import annotations

import logging
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import ReferenceRange
from domains.registry import all_domains

logger = logging.getLogger(__name__)


def seed_domain(session: Session, domain_key: str, rows: List[Dict]) -> int:
    """
    Idempotently upsert one panel's reference ranges (no commit).

    Returns
    -------
    int
        The number of *new* rows inserted (existing rows are updated in place).
    """
    inserted = 0
    for data in rows:
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

    logger.info(
        "Seeded domain %s: %d rows (%d new, %d updated).",
        domain_key, len(rows), inserted, len(rows) - inserted,
    )
    return inserted


def seed_all(session: Session) -> int:
    """
    Idempotently upsert the reference ranges of every registered domain.

    Returns
    -------
    int
        The total number of *new* rows inserted across all panels.
    """
    inserted = 0
    for domain in all_domains():
        inserted += seed_domain(session, domain.key, domain.reference_range_rows())

    session.commit()
    return inserted


def seed_counts() -> Dict[str, int]:
    """``{domain key: number of seed rows}`` — for the runner's report and tests."""
    return {domain.key: len(domain.reference_range_rows()) for domain in all_domains()}
