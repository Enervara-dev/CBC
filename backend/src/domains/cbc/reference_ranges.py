"""
CBC reference-range seed data — the per-panel knowledge consumed by ``db.seeds``.

Design notes
------------
* Age bands are exactly (0–18), (18–65), (65–150) so they line up with
  ``ReferenceRangeLookup._get_age_category`` (which matches on age_min/age_max).
* Sex-specific ranges for HGB / HCT / RBC; unisex (gender NULL) for the rest.
* Conventional units (g/dL, K/uL, fL, pg, %) — matching Indian/US lab reports.

Values are generic adult/pediatric/geriatric references (WHO/CLSI-style). They
are intentionally conservative defaults — adjust per your authoritative guideline
before clinical use. Some pediatric values are placeholders equal to adult where
a single source range is acceptable for v1.

To add a new specialty, copy this file into ``domains/<name>/reference_ranges.py``
and edit the rows (see ``domains/_template/``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

_AGE_BANDS = [(0, 18), (18, 65), (65, 150)]


def _rr(
    code: str,
    gender: Optional[str],
    age_min: int,
    age_max: int,
    low: float,
    high: float,
    unit: str,
    *,
    lab_source: str = "Any",
    condition: Optional[str] = None,
    source_guideline: str = "WHO/CLSI generic",
    version: str = "1.0",
) -> Dict[str, Any]:
    """Build one reference-range row dict."""
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
        "specimen_type": "Whole Blood",
        "source_guideline": source_guideline,
        "version": version,
    }


def reference_range_rows() -> List[Dict[str, Any]]:
    """The full set of seed reference-range rows."""
    rows: List[Dict[str, Any]] = []

    # ── Hemoglobin (g/dL) — sex-specific adult/elderly, unisex pediatric ──────
    rows += [
        _rr("HGB", None, 0, 18, 11.0, 14.0, "g/dL"),
        _rr("HGB", "M", 18, 65, 13.5, 17.5, "g/dL"),
        _rr("HGB", "F", 18, 65, 12.0, 15.5, "g/dL"),
        _rr("HGB", "M", 65, 150, 12.5, 17.0, "g/dL"),
        _rr("HGB", "F", 65, 150, 11.5, 15.5, "g/dL"),
        _rr("HGB", "F", 18, 65, 11.0, 14.0, "g/dL",
            condition="pregnancy", source_guideline="WHO Pregnancy 2022"),
    ]

    # ── Hematocrit (%) ───────────────────────────────────────────────────────
    rows += [
        _rr("HCT", None, 0, 18, 33.0, 45.0, "%"),
        _rr("HCT", "M", 18, 65, 41.0, 53.0, "%"),
        _rr("HCT", "F", 18, 65, 36.0, 46.0, "%"),
        _rr("HCT", "M", 65, 150, 38.0, 52.0, "%"),
        _rr("HCT", "F", 65, 150, 35.0, 46.0, "%"),
    ]

    # ── RBC count (M/uL) ─────────────────────────────────────────────────────
    rows += [
        _rr("RBC", None, 0, 18, 4.0, 5.2, "M/uL"),
        _rr("RBC", "M", 18, 65, 4.5, 5.9, "M/uL"),
        _rr("RBC", "F", 18, 65, 4.0, 5.2, "M/uL"),
        _rr("RBC", "M", 65, 150, 4.2, 5.8, "M/uL"),
        _rr("RBC", "F", 65, 150, 3.9, 5.2, "M/uL"),
    ]

    # ── Unisex markers across all age bands ──────────────────────────────────
    unisex = {
        "WBC":  (4.0, 11.0, "K/uL"),
        "PLT":  (150.0, 400.0, "K/uL"),
        "MCV":  (80.0, 100.0, "fL"),
        "MCH":  (27.0, 33.0, "pg"),
        "MCHC": (32.0, 36.0, "g/dL"),
        "RDW":  (11.5, 14.5, "%"),
        "NEUT": (40.0, 70.0, "%"),
        "LYMPH": (20.0, 40.0, "%"),
    }
    for code, (low, high, unit) in unisex.items():
        for age_min, age_max in _AGE_BANDS:
            rows.append(_rr(code, None, age_min, age_max, low, high, unit))

    # ── Absolute differential counts (K/uL) — age-specific ──────────────────
    # Neutropenia and lymphocytosis are defined on these, not on percentages.
    # Children run higher lymphocyte counts than adults. Benign ethnic
    # neutropenia (ANC 1.0–1.5) is named in the interpretation rather than
    # hidden by lowering the limit for everyone.
    absolute = {
        "ANC": {(0, 18): (1.5, 8.0), (18, 65): (2.0, 7.5), (65, 150): (2.0, 7.5)},
        "ALC": {(0, 18): (1.0, 6.5), (18, 65): (1.0, 4.0), (65, 150): (1.0, 4.0)},
    }
    for code, bands in absolute.items():
        for (age_min, age_max), (low, high) in bands.items():
            rows.append(_rr(code, None, age_min, age_max, low, high, "K/uL",
                            source_guideline="CLSI/CTCAE generic"))

    return rows


__all__ = ["reference_range_rows"]
