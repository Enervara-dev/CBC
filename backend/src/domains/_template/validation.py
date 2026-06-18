"""
<PANEL> Layer-5 validation rules.

Define this panel's severity thresholds, clinical validation rules, impossible
conditions, confidence calibration, and urgency flags, then return them from
``load_validation_rules``. Use ``domains/cbc/validation.py`` as the reference.
"""

from __future__ import annotations

from typing import Any, Dict

SEVERITY_THRESHOLDS: Dict[str, Any] = {}
CLINICAL_VALIDATION_RULES: Dict[str, Any] = {}
IMPOSSIBLE_CONDITIONS: Dict[str, Any] = {}
CONFIDENCE_CALIBRATION: Dict[str, Any] = {}
URGENCY_FLAGS: Dict[str, Any] = {}


def load_validation_rules() -> Dict[str, Any]:
    """Return all Layer-5 validation rule sets in one dict."""
    return {
        "severity_thresholds": SEVERITY_THRESHOLDS,
        "clinical_validation_rules": CLINICAL_VALIDATION_RULES,
        "impossible_conditions": IMPOSSIBLE_CONDITIONS,
        "confidence_calibration": CONFIDENCE_CALIBRATION,
        "urgency_flags": URGENCY_FLAGS,
    }
