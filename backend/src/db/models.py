"""
SQLAlchemy models — the canonical knowledge base that grounds Layer 2.

This relational schema IS the "knowledge-graph-first" foundation: biomarkers and
their aliases, units and conversions, and sex/age-stratified reference ranges.
Every downstream layer queries these tables instead of a black-box model.

Implemented here:
  - BaseModel        abstract mixin: audit timestamps + shared helpers
  - ReferenceRange   sex/age/lab-stratified normal intervals per biomarker

Planned (TODO):
  - Category, Biomarker, BiomarkerAlias, Unit, UnitConversion
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UUID,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import declarative_base, validates

Base = declarative_base()


def _utcnow() -> datetime:
    """Timezone-aware UTC now (used for manual timestamp bumps)."""
    return datetime.now(timezone.utc)


class BaseModel(Base):
    """
    Abstract base providing audit-trail timestamps for every table.

    - ``created_at``: set once by the database on INSERT (``server_default``).
    - ``updated_at``: set on INSERT and refreshed on every UPDATE (``onupdate``).

    Timestamps are DB-authoritative (``func.now()``) so they stay correct even for
    bulk/raw SQL writes that bypass the ORM. Concrete subclasses declare their own
    indexes/constraints in ``__table_args__``.
    """

    __abstract__ = True

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="Row creation timestamp (UTC), set by the database on INSERT.",
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        doc="Last-modified timestamp (UTC); auto-updated on every UPDATE.",
    )

    def touch(self) -> None:
        """Manually bump ``updated_at`` (for bulk updates that skip ORM flush events)."""
        self.updated_at = _utcnow()


class ReferenceRange(BaseModel):
    """
    Sex / age / lab / condition-stratified reference ranges for CBC biomarkers —
    the authoritative "normal interval" source that Layer 2 (normalization) and
    Layer 3 (clinical features) query instead of hard-coded constants.

    Table purpose
    -------------
    One row = one normal interval for a (biomarker, demographic, lab, condition)
    combination. The normalizer resolves a raw extracted value to its canonical
    biomarker, then looks up the single best-matching row here to flag the value
    LOW / NORMAL / HIGH — with full provenance (which guideline, which version).

    Fields
    ------
    id               : UUID surrogate primary key.
    biomarker_id     : canonical biomarker code, e.g. "HGB", "WBC". Soft reference
                       to the biomarker catalog (becomes a FK once that table lands).
    gender           : "M" / "F" / NULL. NULL = applies to any sex.
    age_min, age_max : inclusive age band in YEARS. 0–150 ≈ "all ages".
    lab_source       : issuing lab, e.g. "Quest", "LabCorp", or "Any" (generic range).
    reference_min    : lower bound of the normal interval (NULL = upper-bounded only).
    reference_max    : upper bound of the normal interval (NULL = lower-bounded only).
    unit             : unit the bounds are expressed in, e.g. "g/dL", "K/uL".
    specimen_type    : specimen the range is valid for, e.g. "Whole Blood", "Serum".
    condition        : physiological qualifier, e.g. "pregnancy"; NULL = general pop.
    source_guideline : authority for the values, e.g. "WHO 2022", "CLSI".
    version          : dataset version, for reproducible / audited lookups.
    created_at/updated_at : audit timestamps (see :class:`BaseModel`).

    Constraints & indexes
    ---------------------
    UNIQUE (biomarker_id, gender, age_min, age_max, lab_source, condition)
        One canonical interval per dimensional combination.
        NOTE: PostgreSQL treats NULL as DISTINCT in UNIQUE constraints, so two rows
        with gender=NULL (or condition=NULL) and otherwise-equal keys will NOT
        collide. If you need them to, add ``postgresql_nulls_not_distinct=True``
        (PG 15+) or a COALESCE-based functional unique index.
    INDEX (biomarker_id, gender, age_min, age_max)
        Serves the hot lookup path: filter by biomarker + patient demographics.

    Query patterns
    --------------
    1. Best-matching range for a patient (prefer the most specific row)::

           SELECT * FROM reference_range
            WHERE biomarker_id = :code
              AND (gender = :sex OR gender IS NULL)
              AND :age BETWEEN age_min AND age_max
              AND (condition = :cond OR condition IS NULL)
              AND lab_source IN (:lab, 'Any')
            ORDER BY (gender IS NOT NULL) DESC,     -- prefer sex-specific
                     (lab_source <> 'Any') DESC,    -- prefer lab-specific
                     (condition IS NOT NULL) DESC   -- prefer condition-specific
            LIMIT 1;

    2. All ranges for a biomarker (admin / QA)::  WHERE biomarker_id = :code

    Reference range examples
    ------------------------
        HGB | M    | 18–150y | Any       | 13.5–17.5 g/dL  (WHO 2022)
        HGB | F    | 18–150y | Any       | 12.0–15.5 g/dL  (WHO 2022)
        HGB | F    | 18–45y  | pregnancy | 11.0–14.0 g/dL
        WBC | NULL | 0–150y  | Any       | 4.0–11.0  K/uL
        PLT | NULL | 0–150y  | Any       | 150–400   K/uL
    """

    __tablename__ = "reference_range"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        doc="Surrogate primary key (UUID4).",
    )

    # ── Dimensions (what this range applies to) ──────────────────────────────
    biomarker_id = Column(
        String(32),
        nullable=False,
        doc="Canonical biomarker code, e.g. 'HGB', 'WBC'. Soft FK to biomarker catalog.",
    )
    gender = Column(
        String(1),
        nullable=True,
        doc="'M', 'F', or NULL for a sex-independent (unisex) range.",
    )
    age_min = Column(
        Integer,
        nullable=False,
        doc="Inclusive lower age bound in YEARS (0 = birth).",
    )
    age_max = Column(
        Integer,
        nullable=False,
        doc="Inclusive upper age bound in YEARS (e.g. 150 = effectively no upper limit).",
    )
    lab_source = Column(
        String(64),
        nullable=False,
        default="Any",
        server_default="Any",
        doc="Issuing lab/source, e.g. 'Quest', 'LabCorp', or 'Any' for a generic range.",
    )
    condition = Column(
        String(64),
        nullable=True,
        doc="Physiological condition the range is specific to, e.g. 'pregnancy'; NULL otherwise.",
    )

    # ── The interval itself ──────────────────────────────────────────────────
    reference_min = Column(
        Float,
        nullable=True,
        doc="Lower bound of the normal interval (NULL = one-sided, upper-bounded only).",
    )
    reference_max = Column(
        Float,
        nullable=True,
        doc="Upper bound of the normal interval (NULL = one-sided, lower-bounded only).",
    )
    unit = Column(
        String(32),
        nullable=False,
        doc="Unit the bounds are expressed in, e.g. 'g/dL', 'K/uL'.",
    )
    specimen_type = Column(
        String(64),
        nullable=False,
        default="Whole Blood",
        server_default="Whole Blood",
        doc="Specimen the range is valid for, e.g. 'Whole Blood', 'Serum'.",
    )

    # ── Provenance / audit ───────────────────────────────────────────────────
    source_guideline = Column(
        String(128),
        nullable=True,
        doc="Authoritative source of the values, e.g. 'WHO 2022', 'CLSI'.",
    )
    version = Column(
        String(20),
        nullable=False,
        default="1.0",
        server_default="1.0",
        doc="Version of this reference dataset, for reproducible/audited lookups.",
    )

    __table_args__ = (
        UniqueConstraint(
            "biomarker_id", "gender", "age_min", "age_max", "lab_source", "condition",
            name="uq_reference_range_dimensions",
        ),
        Index(
            "ix_reference_range_lookup",
            "biomarker_id", "gender", "age_min", "age_max",
        ),
        CheckConstraint("age_min >= 0", name="ck_reference_range_age_min_nonneg"),
        CheckConstraint("age_max >= age_min", name="ck_reference_range_age_order"),
        CheckConstraint(
            "gender IN ('M', 'F') OR gender IS NULL",
            name="ck_reference_range_gender",
        ),
        CheckConstraint(
            "reference_min IS NULL OR reference_max IS NULL "
            "OR reference_max >= reference_min",
            name="ck_reference_range_bounds_order",
        ),
    )

    # ── Validation ───────────────────────────────────────────────────────────
    @validates("gender")
    def _validate_gender(self, key: str, value: Optional[str]) -> Optional[str]:
        """Normalize gender to 'M'/'F'/None; reject anything else."""
        if value is None:
            return None
        v = value.strip().upper()
        if v in ("", "U", "ANY", "UNISEX", "NULL"):
            return None
        if v in ("M", "F"):
            return v
        raise ValueError(f"gender must be 'M', 'F', or NULL; got {value!r}")

    @validates("biomarker_id")
    def _normalize_biomarker_id(self, key: str, value: str) -> str:
        """Store biomarker codes upper-cased and trimmed for consistent joins."""
        if value is None:
            raise ValueError("biomarker_id is required")
        return value.strip().upper()

    # ── Helpers ──────────────────────────────────────────────────────────────
    @property
    def is_unisex(self) -> bool:
        """True if this range applies regardless of sex."""
        return self.gender is None

    def applies_to_age(self, age_years: float) -> bool:
        """True if ``age_years`` falls within this range's inclusive age band."""
        return self.age_min <= age_years <= self.age_max

    def matches(
        self,
        *,
        gender: Optional[str],
        age_years: float,
        lab_source: str = "Any",
        condition: Optional[str] = None,
    ) -> bool:
        """
        True if this range is applicable to the given patient context.

        A unisex row (gender NULL) matches any sex; a generic row (lab_source
        'Any') matches any lab; a general row (condition NULL) matches any
        condition. Choosing the *most specific* matching row among candidates is
        the caller's (reference_lookup) responsibility.
        """
        if self.gender is not None and self.gender != gender:
            return False
        if not self.applies_to_age(age_years):
            return False
        if self.lab_source != "Any" and lab_source != "Any" and self.lab_source != lab_source:
            return False
        if self.condition is not None and self.condition != condition:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (audit/logging/JSON)."""
        return {
            "id": str(self.id) if self.id is not None else None,
            "biomarker_id": self.biomarker_id,
            "gender": self.gender,
            "age_min": self.age_min,
            "age_max": self.age_max,
            "lab_source": self.lab_source,
            "reference_min": self.reference_min,
            "reference_max": self.reference_max,
            "unit": self.unit,
            "specimen_type": self.specimen_type,
            "condition": self.condition,
            "source_guideline": self.source_guideline,
            "version": self.version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        g = self.gender or "any"
        return (
            f"<ReferenceRange {self.biomarker_id} "
            f"[{g}, {self.age_min}-{self.age_max}y, {self.lab_source}] "
            f"{self.reference_min}-{self.reference_max} {self.unit}>"
        )
