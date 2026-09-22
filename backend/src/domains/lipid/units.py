"""
Lipid-profile unit-conversion and data-quality tables (keyed by canonical name).

Conversions worth noting
------------------------
* Cholesterol fractions: 1 mmol/L = 38.67 mg/dL (molar mass of cholesterol).
* Triglycerides: 1 mmol/L = 88.57 mg/dL (triglycerides are heavier), so the two
  families must NOT share a factor — mixing them misreads a report by ~2.3×.
* The cholesterol/HDL ratio is unitless.

Absolute limits mark extraction/unit errors; critical values mark real but
urgent results — triglycerides > 1000 mg/dL carries acute pancreatitis risk, and
LDL > 300 mg/dL suggests familial hypercholesterolaemia.
"""

from __future__ import annotations

from typing import Any, Dict

# 1 mmol/L cholesterol = 38.67 mg/dL; 1 mmol/L triglyceride = 88.57 mg/dL
_CHOLESTEROL_UNITS = {"mg/dL": 1.0, "mmol/L": 38.67}
_TRIGLYCERIDE_UNITS = {"mg/dL": 1.0, "mmol/L": 88.57}

# factor = multiplier converting a value FROM this unit TO the standard unit
CONVERSION_FACTORS: Dict[str, Dict[str, float]] = {
    "total_cholesterol": dict(_CHOLESTEROL_UNITS),
    "ldl_cholesterol": dict(_CHOLESTEROL_UNITS),
    "hdl_cholesterol": dict(_CHOLESTEROL_UNITS),
    "vldl_cholesterol": dict(_CHOLESTEROL_UNITS),
    "non_hdl_cholesterol": dict(_CHOLESTEROL_UNITS),
    "triglycerides": dict(_TRIGLYCERIDE_UNITS),
    "cholesterol_hdl_ratio": {"ratio": 1.0},
}

# standard (canonical) unit per biomarker — the one with factor 1.0
STANDARD_UNITS: Dict[str, str] = {
    "total_cholesterol": "mg/dL",
    "ldl_cholesterol": "mg/dL",
    "hdl_cholesterol": "mg/dL",
    "vldl_cholesterol": "mg/dL",
    "non_hdl_cholesterol": "mg/dL",
    "triglycerides": "mg/dL",
    "cholesterol_hdl_ratio": "ratio",
}

# Physiologically possible bounds — outside ⇒ impossible (extraction/unit error)
ABSOLUTE_LIMITS: Dict[str, Dict[str, float]] = {
    "total_cholesterol": {"min": 20.0, "max": 1000.0},
    "ldl_cholesterol": {"min": 5.0, "max": 800.0},
    "hdl_cholesterol": {"min": 2.0, "max": 200.0},
    "vldl_cholesterol": {"min": 1.0, "max": 500.0},
    "non_hdl_cholesterol": {"min": 5.0, "max": 900.0},
    "triglycerides": {"min": 5.0, "max": 10000.0},
    "cholesterol_hdl_ratio": {"min": 0.5, "max": 30.0},
}

# Life-threatening ("panic") thresholds — real but require urgent attention
CRITICAL_VALUES: Dict[str, Dict[str, float]] = {
    "total_cholesterol": {"low": 0.0, "high": 500.0},
    "ldl_cholesterol": {"low": 0.0, "high": 300.0},
    "hdl_cholesterol": {"low": 20.0, "high": 200.0},
    "triglycerides": {"low": 0.0, "high": 1000.0},   # acute pancreatitis risk
}

# Pretty names for human-readable quality messages
DISPLAY_NAMES: Dict[str, str] = {
    "total_cholesterol": "Total Cholesterol",
    "ldl_cholesterol": "LDL Cholesterol",
    "hdl_cholesterol": "HDL Cholesterol",
    "vldl_cholesterol": "VLDL Cholesterol",
    "non_hdl_cholesterol": "Non-HDL Cholesterol",
    "triglycerides": "Triglycerides",
    "cholesterol_hdl_ratio": "Cholesterol/HDL Ratio",
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
