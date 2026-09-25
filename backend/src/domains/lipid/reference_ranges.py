"""
Lipid-profile reference-range seed data — consumed by ``db.seeds``.

Design notes
------------
* Age bands are exactly (0–18), (18–65), (65–150) so they line up with
  ``ReferenceRangeLookup._get_age_category``.
* Lipid "reference ranges" are **desirable/optimal cut-points**, not population
  intervals, so most rows are *one-sided*: only an upper bound (``reference_min``
  is ``NULL``) for the atherogenic markers, and only a lower bound for HDL.
  ``ReferenceRange`` supports one-sided intervals and Layer 2 reads them as
  "never LOW" / "never HIGH" respectively — which is the clinical intent (a
  triglyceride of 60 mg/dL is not an abnormal-low result).
* HDL is the one sex-specific marker (the protective threshold differs).
* Specimen is **Serum**; fasting is assumed for triglycerides.

Cut-points follow NCEP ATP III with the 2018 AHA/ACC cholesterol guideline; they
are conservative defaults — review against the deploying laboratory's reporting
policy before clinical use.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Paediatric rows are written out individually; adults share the same cut-points.
_ADULT_BANDS = [(18, 65), (65, 150)]

_SPECIMEN = "Serum"
_GUIDELINE = "NCEP ATP III / AHA-ACC 2018"


def _rr(
    code: str,
    gender: Optional[str],
    age_min: int,
    age_max: int,
    low: Optional[float],
    high: Optional[float],
    unit: str,
    *,
    lab_source: str = "Any",
    condition: Optional[str] = None,
    source_guideline: str = _GUIDELINE,
    version: str = "1.0",
) -> Dict[str, Any]:
    """Build one reference-range row dict (``low``/``high`` may be ``None``)."""
    return {
        "biomarker_id": code,
        "gender": gender,
        "age_min": age_min,
        "age_max": age_max,
        "reference_min": low,
        "reference_max": high,
        "unit": unit,
        "lab_source": lab_source,
        "condition": condition,
        "specimen_type": _SPECIMEN,
        "source_guideline": source_guideline,
        "version": version,
    }


def reference_range_rows() -> List[Dict[str, Any]]:
    """The full set of seed reference-range rows for the lipid panel."""
    rows: List[Dict[str, Any]] = []

    # ── HDL (mg/dL) — the only marker where LOW is the abnormal side ─────────
    rows += [
        _rr("HDL", None, 0, 18, 45.0, None, "mg/dL"),
        _rr("HDL", "M", 18, 65, 40.0, None, "mg/dL"),
        _rr("HDL", "F", 18, 65, 50.0, None, "mg/dL"),
        _rr("HDL", "M", 65, 150, 40.0, None, "mg/dL"),
        _rr("HDL", "F", 65, 150, 50.0, None, "mg/dL"),
    ]

    # ── Paediatric desirable cut-points (NHLBI, unisex) ──────────────────────
    rows += [
        _rr("CHOL", None, 0, 18, None, 170.0, "mg/dL",
            source_guideline="NHLBI paediatric 2011"),
        _rr("LDL", None, 0, 18, None, 110.0, "mg/dL",
            source_guideline="NHLBI paediatric 2011"),
        _rr("TRIG", None, 0, 18, None, 100.0, "mg/dL",
            source_guideline="NHLBI paediatric 2011"),
        _rr("NONHDL", None, 0, 18, None, 120.0, "mg/dL",
            source_guideline="NHLBI paediatric 2011"),
        _rr("VLDL", None, 0, 18, None, 30.0, "mg/dL"),
        _rr("CHOLHDL", None, 0, 18, None, 4.0, "ratio"),
    ]

    # ── Adult / geriatric desirable cut-points (unisex, upper-bounded) ───────
    adult_upper_bounds = {
        "CHOL":    (200.0, "mg/dL"),   # desirable < 200
        "LDL":     (100.0, "mg/dL"),   # optimal < 100
        "TRIG":    (150.0, "mg/dL"),   # normal (fasting) < 150
        "VLDL":    (30.0, "mg/dL"),
        "NONHDL":  (130.0, "mg/dL"),   # LDL target + 30
        "CHOLHDL": (5.0, "ratio"),     # desirable < 5
    }
    for code, (high, unit) in adult_upper_bounds.items():
        for age_min, age_max in _ADULT_BANDS:
            rows.append(_rr(code, None, age_min, age_max, None, high, unit))

    return rows


__all__ = ["reference_range_rows"]
