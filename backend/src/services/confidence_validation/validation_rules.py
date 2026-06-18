"""
Backwards-compatible shim.

CBC validation rules now live in ``domains.cbc.validation`` (the per-panel
knowledge folder). This module re-exports them so existing imports keep working.
New code should import from ``domains.cbc.validation`` (or via the domain registry).
"""

from domains.cbc.validation import (  # noqa: F401
    SEVERITY_THRESHOLDS,
    CLINICAL_VALIDATION_RULES,
    IMPOSSIBLE_CONDITIONS,
    CONFIDENCE_CALIBRATION,
    URGENCY_FLAGS,
    ClinicalRule,
    load_validation_rules,
)

__all__ = [
    "SEVERITY_THRESHOLDS",
    "CLINICAL_VALIDATION_RULES",
    "IMPOSSIBLE_CONDITIONS",
    "CONFIDENCE_CALIBRATION",
    "URGENCY_FLAGS",
    "ClinicalRule",
    "load_validation_rules",
]
