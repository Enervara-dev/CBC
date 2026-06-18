"""
Reference-range lookup with demographic-aware priority fallback.

Given a biomarker and a patient context (gender, age, optional lab + condition),
find the single most-appropriate reference range from the ``reference_range``
table, progressively relaxing specificity until a row matches:

    1. exact      : lab + gender + age + condition
    2. no-condition: lab + gender + age
    3. generic lab : "Any" lab + gender + age
    4. unisex      : gender NULL + age
    5. (no match)  -> ValueError

Each tier is an indexed equality query on (biomarker_id, gender, age_min,
age_max), so lookups stay on the ``ix_reference_range_lookup`` index.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ReferenceRange

logger = logging.getLogger(__name__)


class ReferenceRangeLookup:
    """
    Resolve the applicable reference range for a biomarker + patient context.

    Parameters
    ----------
    db_session : AsyncSession
        An async SQLAlchemy session bound to the knowledge-base database.

    Usage
    -----
    >>> lookup = ReferenceRangeLookup(session)
    >>> await lookup.get_reference_range("HGB", gender="F", age=30, lab_source="Quest")
    {'biomarker_id': 'HGB', 'ref_min': 12.0, 'ref_max': 16.0, 'unit': 'g/dL', ...}
    """

    def __init__(self, db_session: AsyncSession) -> None:
        self.db: AsyncSession = db_session

    # ── Public API ───────────────────────────────────────────────────────────
    async def get_reference_range(
        self,
        biomarker_id: str,
        gender: str,
        age: int,
        lab_source: str = "Any",
        condition: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Return the best-matching reference range for the given patient context.

        Parameters
        ----------
        biomarker_id : str
            Canonical biomarker code, e.g. ``"HGB"`` (matched case-insensitively).
        gender : str
            ``"M"`` or ``"F"`` (case-insensitive).
        age : int
            Patient age in years; bucketed via :meth:`_get_age_category`.
        lab_source : str
            Preferred issuing lab, e.g. ``"Quest"``. Defaults to ``"Any"``.
        condition : Optional[str]
            Physiological qualifier, e.g. ``"pregnancy"``. Defaults to ``None``.

        Returns
        -------
        Dict[str, Any]
            ``{biomarker_id, ref_min, ref_max, unit, gender, age_range,
               lab_source, source_guideline}``.

        Raises
        ------
        ValueError
            If no reference range matches any fallback tier.
        RuntimeError
            If the database query fails.
        """
        bid = biomarker_id.strip().upper()
        g = gender.strip().upper() if gender else None

        # Priority chain: (description, gender, lab_source, condition).
        # Each step relaxes one dimension: condition → lab → gender.
        candidates: List[Tuple[str, Optional[str], str, Optional[str]]] = [
            ("exact (lab+gender+age+condition)", g, lab_source, condition),
            ("lab+gender+age (no condition)", g, lab_source, None),
            ("generic Any-lab+gender+age", g, "Any", None),
            ("unisex (gender NULL)+age", None, "Any", None),
        ]
        plan = self._dedupe(candidates)

        logger.debug(
            "Reference-range lookup: biomarker=%s gender=%s age=%s lab=%s condition=%s",
            bid, gender, age, lab_source, condition,
        )

        try:
            for desc, tier_gender, tier_lab, tier_condition in plan:
                stmt = self._build_query(bid, tier_gender, age, tier_lab, tier_condition)
                result = await self.db.execute(stmt)
                row = result.scalars().first()
                if row is not None:
                    logger.info(
                        "Reference range matched [%s] for %s (gender=%s, age=%s, lab=%s)",
                        desc, bid, gender, age, lab_source,
                    )
                    return self._format_result(row)
                logger.debug("No match at tier [%s] for %s — falling back", desc, bid)
        except SQLAlchemyError as exc:
            logger.exception("Database error during reference-range lookup for %s", bid)
            raise RuntimeError(
                f"Database error during reference-range lookup for {biomarker_id!r}"
            ) from exc

        raise ValueError(
            f"No reference range found for biomarker={biomarker_id!r}, "
            f"gender={gender!r}, age={age}, lab_source={lab_source!r}, "
            f"condition={condition!r}"
        )

    # ── Helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _get_age_category(age: int) -> Tuple[int, int]:
        """
        Bucket an age into a standard reference band ``(age_min, age_max)``.

          age < 18   -> (0, 18)    pediatric
          18 ≤ age ≤ 65 -> (18, 65)  adult
          age > 65   -> (65, 150)  geriatric
        """
        if age < 18:
            return (0, 18)
        if age <= 65:
            return (18, 65)
        return (65, 150)

    def _build_query(
        self,
        biomarker_id: str,
        gender: Optional[str],
        age: int,
        lab_source: str,
        condition: Optional[str],
    ):
        """
        Build the indexed SELECT for one fallback tier.

        ``gender``/``condition`` of ``None`` are matched as SQL ``IS NULL`` (i.e.
        unisex / no-condition rows); otherwise equality. Filters are ordered to
        ride the ``(biomarker_id, gender, age_min, age_max)`` index.
        """
        age_min, age_max = self._get_age_category(age)

        stmt = select(ReferenceRange).where(
            ReferenceRange.biomarker_id == biomarker_id,
            ReferenceRange.age_min == age_min,
            ReferenceRange.age_max == age_max,
            ReferenceRange.lab_source == lab_source,
        )

        if gender is None:
            stmt = stmt.where(ReferenceRange.gender.is_(None))
        else:
            stmt = stmt.where(ReferenceRange.gender == gender)

        if condition is None:
            stmt = stmt.where(ReferenceRange.condition.is_(None))
        else:
            stmt = stmt.where(ReferenceRange.condition == condition)

        # Deterministic pick if several versions coexist: newest first.
        return stmt.order_by(ReferenceRange.updated_at.desc()).limit(1)

    @staticmethod
    def _format_result(db_row: ReferenceRange) -> Dict[str, Any]:
        """Convert a :class:`ReferenceRange` ORM row to the output dict."""
        return {
            "biomarker_id": db_row.biomarker_id,
            "ref_min": db_row.reference_min,
            "ref_max": db_row.reference_max,
            "unit": db_row.unit,
            "gender": db_row.gender,
            "age_range": f"{db_row.age_min}-{db_row.age_max}",
            "lab_source": db_row.lab_source,
            "source_guideline": db_row.source_guideline,
        }

    @staticmethod
    def _dedupe(
        candidates: List[Tuple[str, Optional[str], str, Optional[str]]],
    ) -> List[Tuple[str, Optional[str], str, Optional[str]]]:
        """Drop tiers that are identical in (gender, lab, condition) to avoid
        redundant queries (e.g. tier 1 == tier 2 when condition is None)."""
        seen: set = set()
        plan: List[Tuple[str, Optional[str], str, Optional[str]]] = []
        for desc, g, lab, cond in candidates:
            key = (g, lab, cond)
            if key in seen:
                continue
            seen.add(key)
            plan.append((desc, g, lab, cond))
        return plan
