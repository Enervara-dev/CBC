"""
LFT reference-range seed data — the per-panel knowledge consumed by ``db.seeds``.

Design notes
------------
* Age bands are exactly (0–18), (18–65), (65–150) so they line up with
  ``ReferenceRangeLookup._get_age_category`` (which matches on age_min/age_max).
* Sex-specific ranges for the enzymes (ALT / AST / ALP / GGT — men run higher);
  unisex (gender NULL) for bilirubin, proteins, and the A/G ratio.
* Specimen is **Serum** (CBC's rows are Whole Blood) — the column exists on
  ``ReferenceRange`` so both panels coexist in the same table.
* Conventional units (U/L, mg/dL, g/dL) — matching Indian/US lab reports.
* Paediatric ALP is deliberately much higher: bone growth, not liver disease.
* Two pregnancy rows (``condition="pregnancy"``) keep normal physiology from
  reading as liver disease: placental ALP roughly doubles by the third trimester,
  and albumin falls with plasma-volume expansion. The lookup prefers a
  condition-specific row when the caller passes ``condition``.

Values are generic adult/paediatric/geriatric references (IFCC/AASLD-style
laboratory intervals). They are conservative defaults — review them against the
deploying laboratory's authoritative ranges before clinical use.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

_AGE_BANDS = [(0, 18), (18, 65), (65, 150)]

_SPECIMEN = "Serum"
_GUIDELINE = "IFCC/AASLD generic"


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
    """The full set of seed reference-range rows for the liver panel."""
    rows: List[Dict[str, Any]] = []

    # ── ALT / SGPT (U/L) — sex-specific adult; children run slightly lower ───
    rows += [
        _rr("ALT", None, 0, 18, 5.0, 45.0, "U/L"),
        _rr("ALT", "M", 18, 65, 10.0, 40.0, "U/L"),
        _rr("ALT", "F", 18, 65, 7.0, 35.0, "U/L"),
        _rr("ALT", "M", 65, 150, 10.0, 40.0, "U/L"),
        _rr("ALT", "F", 65, 150, 7.0, 35.0, "U/L"),
    ]

    # ── AST / SGOT (U/L) ─────────────────────────────────────────────────────
    rows += [
        _rr("AST", None, 0, 18, 10.0, 60.0, "U/L"),
        _rr("AST", "M", 18, 65, 10.0, 40.0, "U/L"),
        _rr("AST", "F", 18, 65, 9.0, 32.0, "U/L"),
        _rr("AST", "M", 65, 150, 10.0, 40.0, "U/L"),
        _rr("AST", "F", 65, 150, 9.0, 32.0, "U/L"),
    ]

    # ── ALP (U/L) — paediatric values are high from bone growth, not liver ───
    rows += [
        _rr("ALP", None, 0, 18, 100.0, 400.0, "U/L",
            source_guideline="IFCC paediatric (bone growth)"),
        _rr("ALP", "M", 18, 65, 40.0, 129.0, "U/L"),
        _rr("ALP", "F", 18, 65, 35.0, 104.0, "U/L"),
        _rr("ALP", "M", 65, 150, 40.0, 150.0, "U/L"),
        _rr("ALP", "F", 65, 150, 35.0, 140.0, "U/L"),
        _rr("ALP", "F", 18, 65, 40.0, 250.0, "U/L",
            condition="pregnancy", source_guideline="Placental ALP (3rd trimester)"),
    ]

    # ── GGT (U/L) ────────────────────────────────────────────────────────────
    rows += [
        _rr("GGT", None, 0, 18, 5.0, 35.0, "U/L"),
        _rr("GGT", "M", 18, 65, 8.0, 61.0, "U/L"),
        _rr("GGT", "F", 18, 65, 5.0, 36.0, "U/L"),
        _rr("GGT", "M", 65, 150, 8.0, 61.0, "U/L"),
        _rr("GGT", "F", 65, 150, 5.0, 36.0, "U/L"),
    ]

    # ── Albumin (g/dL) — falls with age and with synthetic dysfunction ───────
    rows += [
        _rr("ALB", None, 0, 18, 3.8, 5.4, "g/dL"),
        _rr("ALB", None, 18, 65, 3.5, 5.2, "g/dL"),
        _rr("ALB", None, 65, 150, 3.2, 4.6, "g/dL"),
        _rr("ALB", "F", 18, 65, 2.8, 4.5, "g/dL",
            condition="pregnancy", source_guideline="Pregnancy haemodilution"),
    ]

    # ── Unisex markers, same interval across all age bands ───────────────────
    unisex = {
        "TBIL": (0.2, 1.2, "mg/dL"),
        "DBIL": (0.0, 0.3, "mg/dL"),
        "IBIL": (0.1, 0.9, "mg/dL"),
        "TP":   (6.0, 8.3, "g/dL"),
        "GLOB": (2.0, 3.5, "g/dL"),
        "AGR":  (1.0, 2.5, "ratio"),
    }
    for code, (low, high, unit) in unisex.items():
        for age_min, age_max in _AGE_BANDS:
            rows.append(_rr(code, None, age_min, age_max, low, high, unit))

    return rows


__all__ = ["reference_range_rows"]
