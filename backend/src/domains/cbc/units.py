"""
CBC unit-conversion and data-quality tables.

Layer 2 keys both services by the *canonical name* (``CODE_TO_NAME`` values), so
every table here is keyed by e.g. ``"hemoglobin"``, not ``"HGB"``:

* ``CONVERSION_FACTORS`` / ``STANDARD_UNITS`` — consumed by ``UnitConverter``.
  A factor multiplies a value in that unit to express it in the standard unit,
  so the standard unit always has factor ``1.0``.
* ``ABSOLUTE_LIMITS`` / ``CRITICAL_VALUES`` / ``DISPLAY_NAMES`` — consumed by
  ``DataQualityChecker`` (physiological possibility, then panic values).

These lived inside the Layer-2 services until LFT/Lipid arrived; they are panel
knowledge, so they belong with the panel. The services now merge this table
across every registered domain.
"""

from __future__ import annotations

from typing import Any, Dict

# factor = multiplier converting a value FROM this unit TO the standard unit
#
# The "/cumm" family below is what Indian laboratories actually print, and getting
# it wrong is not subtle: a platelet count reported as "2.91 lakhs/cumm" (291
# K/uL, normal) read as 2.91 K/uL is critical thrombocytopenia, and "288000
# Cells/cumm" (288 K/uL, normal) read verbatim breaches the absolute maximum.
# 1 lakh = 100,000, so lakhs/cumm → K/uL is x100; cells/cumm → K/uL is x0.001.
# Unit matching is case-insensitive, so one spelling of each is enough.
CONVERSION_FACTORS: Dict[str, Dict[str, float]] = {
    "hemoglobin": {"g/dL": 1.0, "g/L": 0.1, "mmol/L": 0.621,
                   "gm/dL": 1.0, "gm%": 1.0, "g%": 1.0, "gms%": 1.0},
    "wbc":        {"K/uL": 1.0, "10^3/uL": 1.0, "10^9/L": 1.0,
                   "cells/cumm": 0.001, "/cumm": 0.001, "cells/cmm": 0.001,
                   "X1000cells/cumm": 1.0, "thousands/cumm": 1.0, "10^3/cumm": 1.0},
    "mcv":        {"fL": 1.0, "um^3": 1.0, "fl": 1.0, "cu.microns": 1.0},
    "mch":        {"pg": 1.0, "fmol": 0.0621, "pgm": 1.0},
    "mchc":       {"g/dL": 1.0, "g/L": 0.1, "gm/dL": 1.0, "gm%": 1.0, "gms%": 1.0},
    "rbc":        {"M/uL": 1.0, "10^6/uL": 1.0, "10^12/L": 1.0,
                   "millions/cumm": 1.0, "mill/cumm": 1.0, "million/cumm": 1.0,
                   "10^6/cumm": 1.0},
    "platelets":  {"K/uL": 1.0, "10^3/uL": 1.0, "10^9/L": 1.0,
                   "cells/cumm": 0.001, "/cumm": 0.001, "cells/cmm": 0.001,
                   "lakhs/cumm": 100.0, "lakh/cumm": 100.0, "lacs/cumm": 100.0,
                   "X1000cells/cumm": 1.0, "thousands/cumm": 1.0, "10^3/cumm": 1.0},
    "hematocrit": {"%": 1.0, "L/L": 100.0, "fraction": 100.0, "vol%": 1.0},
    "rdw":        {"%": 1.0},
    "neutrophils": {"%": 1.0},
    "lymphocytes": {"%": 1.0},
    # Absolute differential counts are printed in the same units as the WBC.
    "absolute_neutrophils": {"K/uL": 1.0, "10^3/uL": 1.0, "10^9/L": 1.0,
                             "cells/cumm": 0.001, "/cumm": 0.001, "cells/cmm": 0.001,
                             "X1000cells/cumm": 1.0, "thousands/cumm": 1.0, "10^3/cumm": 1.0},
    "absolute_lymphocytes": {"K/uL": 1.0, "10^3/uL": 1.0, "10^9/L": 1.0,
                             "cells/cumm": 0.001, "/cumm": 0.001, "cells/cmm": 0.001,
                             "X1000cells/cumm": 1.0, "thousands/cumm": 1.0, "10^3/cumm": 1.0},
}

# standard (canonical) unit per biomarker — the one with factor 1.0
STANDARD_UNITS: Dict[str, str] = {
    "hemoglobin": "g/dL",
    "wbc": "K/uL",
    "mcv": "fL",
    "mch": "pg",
    "mchc": "g/dL",
    "rbc": "M/uL",
    "platelets": "K/uL",
    "hematocrit": "%",
    "rdw": "%",
    "neutrophils": "%",
    "lymphocytes": "%",
    "absolute_neutrophils": "K/uL",
    "absolute_lymphocytes": "K/uL",
}

# Physiologically possible bounds — outside ⇒ impossible (extraction/unit error)
ABSOLUTE_LIMITS: Dict[str, Dict[str, float]] = {
    "hemoglobin": {"min": 0.5, "max": 25.0},
    "wbc":        {"min": 0.1, "max": 100.0},
    "platelets":  {"min": 1,   "max": 2000},
    "hematocrit": {"min": 1,   "max": 90},
    "absolute_neutrophils": {"min": 0.0, "max": 100.0},
    "absolute_lymphocytes": {"min": 0.0, "max": 100.0},
}

# Life-threatening ("panic") thresholds — real but require urgent attention
CRITICAL_VALUES: Dict[str, Dict[str, float]] = {
    "hemoglobin": {"low": 3.0, "high": 20.0},
    "wbc":        {"low": 0.5, "high": 50.0},
    "platelets":  {"low": 10,  "high": 1500},
}

# Pretty names for human-readable quality messages
DISPLAY_NAMES: Dict[str, str] = {
    "hemoglobin": "Hemoglobin",
    "wbc": "WBC",
    "platelets": "Platelets",
    "hematocrit": "Hematocrit",
    "absolute_neutrophils": "Absolute neutrophil count",
    "absolute_lymphocytes": "Absolute lymphocyte count",
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
