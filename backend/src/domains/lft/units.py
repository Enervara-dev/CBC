"""
LFT unit-conversion and data-quality tables (keyed by canonical name).

Conversions worth noting
------------------------
* Enzymes report in U/L; ``IU/L`` and ``U/l`` are the same magnitude.
* Bilirubin: 1 mg/dL = 17.104 µmol/L, so µmol/L → mg/dL multiplies by 0.05847.
* Proteins: 1 g/dL = 10 g/L, so g/L → g/dL multiplies by 0.1.

Absolute limits mark extraction/unit errors (an ALT of 10^6 is a parsing fault);
critical values mark real but urgent results (ALT > 1000 U/L = acute hepatitis /
ischaemic or toxic injury; bilirubin > 15 mg/dL; albumin < 1.5 g/dL).
"""

from __future__ import annotations

from typing import Any, Dict

_ENZYME_UNITS = {"U/L": 1.0, "IU/L": 1.0, "U/l": 1.0}
_BILIRUBIN_UNITS = {"mg/dL": 1.0, "umol/L": 0.05847, "µmol/L": 0.05847}
# "gm/dL" / "gm%" are the spellings Indian laboratories print for g/dL.
_PROTEIN_UNITS = {"g/dL": 1.0, "g/L": 0.1, "gm/dL": 1.0, "gm%": 1.0, "gms%": 1.0}

# factor = multiplier converting a value FROM this unit TO the standard unit
CONVERSION_FACTORS: Dict[str, Dict[str, float]] = {
    "alt": dict(_ENZYME_UNITS),
    "ast": dict(_ENZYME_UNITS),
    "alp": dict(_ENZYME_UNITS),
    "ggt": dict(_ENZYME_UNITS),
    "total_bilirubin": dict(_BILIRUBIN_UNITS),
    "direct_bilirubin": dict(_BILIRUBIN_UNITS),
    "indirect_bilirubin": dict(_BILIRUBIN_UNITS),
    "total_protein": dict(_PROTEIN_UNITS),
    "albumin": dict(_PROTEIN_UNITS),
    "globulin": dict(_PROTEIN_UNITS),
    "ag_ratio": {"ratio": 1.0},
}

# standard (canonical) unit per biomarker — the one with factor 1.0
STANDARD_UNITS: Dict[str, str] = {
    "alt": "U/L",
    "ast": "U/L",
    "alp": "U/L",
    "ggt": "U/L",
    "total_bilirubin": "mg/dL",
    "direct_bilirubin": "mg/dL",
    "indirect_bilirubin": "mg/dL",
    "total_protein": "g/dL",
    "albumin": "g/dL",
    "globulin": "g/dL",
    "ag_ratio": "ratio",
}

# Physiologically possible bounds — outside ⇒ impossible (extraction/unit error)
ABSOLUTE_LIMITS: Dict[str, Dict[str, float]] = {
    "alt": {"min": 1.0, "max": 20000.0},
    "ast": {"min": 1.0, "max": 20000.0},
    "alp": {"min": 5.0, "max": 5000.0},
    "ggt": {"min": 1.0, "max": 5000.0},
    "total_bilirubin": {"min": 0.0, "max": 60.0},
    "direct_bilirubin": {"min": 0.0, "max": 40.0},
    "indirect_bilirubin": {"min": 0.0, "max": 40.0},
    "total_protein": {"min": 1.0, "max": 12.0},
    "albumin": {"min": 0.5, "max": 7.0},
    "globulin": {"min": 0.2, "max": 8.0},
    "ag_ratio": {"min": 0.05, "max": 10.0},
}

# Life-threatening ("panic") thresholds — real but require urgent attention
CRITICAL_VALUES: Dict[str, Dict[str, float]] = {
    "alt": {"low": 0.0, "high": 1000.0},
    "ast": {"low": 0.0, "high": 1000.0},
    "alp": {"low": 0.0, "high": 1000.0},
    "total_bilirubin": {"low": 0.0, "high": 15.0},
    "albumin": {"low": 1.5, "high": 7.0},
}

# Pretty names for human-readable quality messages
DISPLAY_NAMES: Dict[str, str] = {
    "alt": "ALT (SGPT)",
    "ast": "AST (SGOT)",
    "alp": "Alkaline Phosphatase",
    "ggt": "GGT",
    "total_bilirubin": "Total Bilirubin",
    "direct_bilirubin": "Direct Bilirubin",
    "indirect_bilirubin": "Indirect Bilirubin",
    "total_protein": "Total Protein",
    "albumin": "Albumin",
    "globulin": "Globulin",
    "ag_ratio": "A/G Ratio",
}


def load_unit_rules() -> Dict[str, Any]:
    """Return this panel's unit + data-quality tables in one dict."""
    return {
        "conversion_factors": CONVERSION_FACTORS,
        "standard_units": STANDARD_UNITS,
        "absolute_limits": ABSOLUTE_LIMITS,
        "critical_values": CRITICAL_VALUES,
        "display_names": DISPLAY_NAMES,
    }


__all__ = [
    "CONVERSION_FACTORS",
    "STANDARD_UNITS",
    "ABSOLUTE_LIMITS",
    "CRITICAL_VALUES",
    "DISPLAY_NAMES",
    "load_unit_rules",
]
