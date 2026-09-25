"""
Backwards-compatible shim.

CBC validation rules now live in ``domains.cbc.validation`` (the per-panel
knowledge folder). This module re-exports them so existing imports keep working.
New code should import from ``domains.cbc.validation`` (or via the domain registry).

Note: these are **CBC only**. The running pipeline is multi-panel and builds its
Layer-3 / Layer-5 knowledge from ``domains.registry.merged_feature_registry()``
and ``merged_validation_rules()``, which cover CBC, LFT, Lipid, and any panel
registered later.
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
