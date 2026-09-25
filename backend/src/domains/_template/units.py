"""
<PANEL> unit-conversion and data-quality tables.

Every table here is keyed by the *canonical name* (a ``CODE_TO_NAME`` value from
``biomarkers.py``), because that is what Layer 2's ``UnitConverter`` and
``DataQualityChecker`` are keyed by. Use ``domains/cbc/units.py`` (or
``domains/lft/units.py`` for non-haematology units) as the worked reference.

* ``CONVERSION_FACTORS`` — name → {unit: factor}; a factor multiplies a value in
  that unit to express it in the standard unit, so the standard unit is ``1.0``.
* ``STANDARD_UNITS``     — name → the standard (factor-1.0) unit.
* ``ABSOLUTE_LIMITS``    — name → {"min", "max"}: physiologically possible bounds;
  outside these a value is treated as an extraction/unit error.
* ``CRITICAL_VALUES``    — name → {"low", "high"}: real but life-threatening.
* ``DISPLAY_NAMES``      — name → human label used in quality messages.

A marker absent from a table is simply not converted / not checked at that tier.
``check_domain`` verifies every key here is a real canonical name.
"""

from __future__ import annotations

from typing import Any, Dict

CONVERSION_FACTORS: Dict[str, Dict[str, float]] = {}
STANDARD_UNITS: Dict[str, str] = {}
ABSOLUTE_LIMITS: Dict[str, Dict[str, float]] = {}
CRITICAL_VALUES: Dict[str, Dict[str, float]] = {}
DISPLAY_NAMES: Dict[str, str] = {}


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
