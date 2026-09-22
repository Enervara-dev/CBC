"""
Lipid-profile clinical validation rules, severity thresholds, and confidence
calibration (Layer 5 knowledge base).

Same declarative shapes as ``domains/cbc/validation.py`` — the Layer-5 engine is
panel-agnostic and reads whatever the registry hands it, so the keys here must
match the CBC bundle's keys exactly.

Clinical references
-------------------
* Risk categories and cut-points: NCEP ATP III; 2018 AHA/ACC/multisociety
  cholesterol guideline (LDL ≥ 190 mg/dL → familial hypercholesterolaemia workup).
* Triglycerides ≥ 1000 mg/dL is the widely used pancreatitis-risk action
  threshold (Endocrine Society 2012).
Values are conservative defaults and MUST be reviewed against the deploying
laboratory's reporting policy before clinical use.

A note on direction: for lipids the *high* side carries the risk for every marker
except HDL, where the *low* side does. The threshold tables below reflect that —
HDL's critical band is on the low side only.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# ─────────────────────────────────────────────────────────────────────────────
# 1. SEVERITY_THRESHOLDS
#    canonical name -> severity band -> {"low": (lo, hi), "high": (lo, hi)} ;
#    plus a flat "normal" (lo, hi). Ranges are half-open [lo, hi).
# ─────────────────────────────────────────────────────────────────────────────
SEVERITY_THRESHOLDS: Dict[str, Dict[str, Any]] = {
    # Total cholesterol (mg/dL) — a risk marker, never a critical call; >= 300
    # suggests a familial disorder and is urgent for specialist review.
    "total_cholesterol": {
        "urgent":   {"low": (0.0, 0.0), "high": (300.0, 1000.0)},
        "routine":  {"low": (0.0, 120.0), "high": (200.0, 300.0)},
        "normal":   (120.0, 200.0),
    },
    # LDL (mg/dL) — >= 190 triggers the familial hypercholesterolaemia work-up
    # (AHA/ACC 2018): urgent for referral, not an emergency.
    "ldl_cholesterol": {
        "urgent":   {"low": (0.0, 0.0), "high": (190.0, 800.0)},
        "routine":  {"low": (0.0, 40.0), "high": (100.0, 190.0)},
        "normal":   (40.0, 100.0),
    },
    # HDL (mg/dL) — risk sits on the LOW side, and the sex-specific limit (40 men,
    # 50 women) comes from the reference range. Below 20 suggests a genetic HDL
    # disorder or an artefact of very high triglycerides.
    "hdl_cholesterol": {
        "urgent":   {"low": (0.0, 20.0), "high": (0.0, 0.0)},
        "routine":  {"low": (20.0, 40.0), "high": (100.0, 300.0)},
        "normal":   (40.0, 100.0),
    },
    # Triglycerides (mg/dL) — >= 500 severe, >= 1000 acute pancreatitis risk
    "triglycerides": {
        "critical": {"low": (0.0, 0.0), "high": (1000.0, 10000.0)},
        "urgent":   {"low": (0.0, 0.0), "high": (500.0, 1000.0)},
        "routine":  {"low": (0.0, 40.0), "high": (150.0, 500.0)},
        "normal":   (40.0, 150.0),
    },
    # VLDL (mg/dL) — tracks triglycerides; nominal bands
    "vldl_cholesterol": {
        "routine":  {"low": (0.0, 2.0), "high": (30.0, 500.0)},
        "normal":   (2.0, 30.0),
    },
    # Non-HDL cholesterol (mg/dL)
    "non_hdl_cholesterol": {
        "urgent":   {"low": (0.0, 0.0), "high": (220.0, 900.0)},
        "routine":  {"low": (0.0, 60.0), "high": (130.0, 220.0)},
        "normal":   (60.0, 130.0),
    },
    # Total cholesterol / HDL ratio (unitless)
    "cholesterol_hdl_ratio": {
        "urgent":   {"low": (0.0, 0.0), "high": (8.0, 30.0)},
        "routine":  {"low": (0.0, 2.0), "high": (5.0, 8.0)},
        "normal":   (2.0, 5.0),
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# 2. CLINICAL_VALIDATION_RULES
#    finding -> evidence requirements + thresholds for the finding to validate.
#    Finding ids use Layer-3 binary feature ids (e.g. "ldl_cholesterol_high").
# ─────────────────────────────────────────────────────────────────────────────
CLINICAL_VALIDATION_RULES: Dict[str, Dict[str, Any]] = {
    "hypercholesterolemia": {
        "required_findings": ["total_cholesterol_high"],
        "optional_findings": ["ldl_cholesterol_high", "non_hdl_cholesterol_high"],
        "contradictory_findings": ["total_cholesterol_low"],
        "min_confidence": 0.72,
        "severity_factor": 0.95,
    },
    "elevated_ldl": {
        "required_findings": ["ldl_cholesterol_high"],
        "optional_findings": ["total_cholesterol_high", "non_hdl_cholesterol_high",
                              "cholesterol_hdl_ratio_high"],
        "contradictory_findings": ["ldl_cholesterol_low"],
        "min_confidence": 0.72,
        "severity_factor": 0.95,
    },
    "familial_hypercholesterolemia_suspected": {
        "required_findings": ["ldl_cholesterol_high"],
        "optional_findings": ["total_cholesterol_high", "non_hdl_cholesterol_high"],
        "contradictory_findings": ["ldl_cholesterol_low"],
        "min_confidence": 0.80,
        "severity_factor": 0.9,
    },
    "hypertriglyceridemia": {
        "required_findings": ["triglycerides_high"],
        "optional_findings": ["vldl_cholesterol_high", "hdl_cholesterol_low"],
        "contradictory_findings": ["triglycerides_low"],
        "min_confidence": 0.72,
        "severity_factor": 0.95,
    },
    "severe_hypertriglyceridemia": {
        "required_findings": ["triglycerides_high"],
        "optional_findings": ["vldl_cholesterol_high"],
        "contradictory_findings": ["triglycerides_low"],
        "min_confidence": 0.80,
        "severity_factor": 0.85,   # pancreatitis risk — scrutinise before acting
    },
    "low_hdl": {
        "required_findings": ["hdl_cholesterol_low"],
        "optional_findings": ["triglycerides_high", "cholesterol_hdl_ratio_high"],
        "contradictory_findings": ["hdl_cholesterol_high"],
        "min_confidence": 0.70,
        "severity_factor": 1.0,
    },
    "atherogenic_dyslipidemia": {
        "required_findings": ["triglycerides_high", "hdl_cholesterol_low"],
        "optional_findings": ["vldl_cholesterol_high", "non_hdl_cholesterol_high"],
        "contradictory_findings": ["hdl_cholesterol_high"],
        "min_confidence": 0.75,
        "severity_factor": 0.9,
    },
    "mixed_dyslipidemia": {
        "required_findings": ["ldl_cholesterol_high", "triglycerides_high"],
        "optional_findings": ["total_cholesterol_high", "hdl_cholesterol_low"],
        "contradictory_findings": [],
        "min_confidence": 0.72,
        "severity_factor": 0.9,
    },
    "elevated_cardiovascular_risk": {
        "required_findings": ["cholesterol_hdl_ratio_high"],
        "optional_findings": ["ldl_cholesterol_high", "non_hdl_cholesterol_high",
                              "hdl_cholesterol_low"],
        "contradictory_findings": [],
        "min_confidence": 0.70,
        "severity_factor": 0.95,
    },
    "hypocholesterolemia": {
        "required_findings": ["total_cholesterol_low"],
        "optional_findings": ["ldl_cholesterol_low"],
        "contradictory_findings": ["total_cholesterol_high"],
        "min_confidence": 0.65,
        "severity_factor": 1.0,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# 3. IMPOSSIBLE_CONDITIONS
#    Mutually-exclusive findings that cannot coexist → flag a conflict.
# ─────────────────────────────────────────────────────────────────────────────
IMPOSSIBLE_CONDITIONS: Dict[str, Dict[str, Any]] = {
    "impossible_total_cholesterol_high_and_low": {
        "conditions": ["total_cholesterol_high", "total_cholesterol_low"],
        "action": "FLAG_CONFLICT",
        "severity": "high",
    },
    "impossible_ldl_high_and_low": {
        "conditions": ["ldl_cholesterol_high", "ldl_cholesterol_low"],
        "action": "FLAG_CONFLICT",
        "severity": "high",
    },
    "impossible_hdl_high_and_low": {
        "conditions": ["hdl_cholesterol_low", "hdl_cholesterol_high"],
        "action": "FLAG_CONFLICT",
        "severity": "high",
    },
    "impossible_triglycerides_high_and_low": {
        "conditions": ["triglycerides_high", "triglycerides_low"],
        "action": "FLAG_CONFLICT",
        "severity": "high",
    },
    "impossible_hyper_and_hypocholesterolemia": {
        "conditions": ["hypercholesterolemia", "hypocholesterolemia"],
        "action": "FLAG_CONFLICT",
        "severity": "high",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# 4. CONFIDENCE_CALIBRATION
#    Multipliers applied to a finding's confidence based on evidence quality.
#    (Panel-independent shape; kept per-domain so a panel can tune it.)
# ─────────────────────────────────────────────────────────────────────────────
CONFIDENCE_CALIBRATION: Dict[str, Dict[str, float]] = {
    "severity_factor": {
        "critical": 0.85,
        "urgent": 0.90,
        "routine": 0.95,
        "normal": 1.0,
    },
    "evidence_count_factor": {
        "1_evidence": 0.70,
        "2_evidence": 0.85,
        "3_or_more_evidence": 0.95,
    },
    "consistency_factor": {
        "all_consistent": 1.0,
        "mostly_consistent": 0.90,
        "conflicting": 0.70,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# 5. URGENCY_FLAGS
#    severity → clinical urgency, notification channel, and escalation path.
# ─────────────────────────────────────────────────────────────────────────────
URGENCY_FLAGS: Dict[str, Dict[str, Optional[str]]] = {
    "critical": {
        "urgency": "STAT",                       # e.g. TG > 1000 — pancreatitis risk
        "clinician_notification": "EMAIL + SMS",
        "escalation": "LIPID_CLINIC_CONSULT",
    },
    "urgent": {
        "urgency": "URGENT",                     # within 24 hours
        "clinician_notification": "EMAIL",
        "escalation": "CARDIOVASCULAR_RISK_REVIEW",
    },
    "routine": {
        "urgency": "ROUTINE",                    # standard review
        "clinician_notification": "PORTAL",
        "escalation": None,
    },
    "normal": {
        "urgency": "ROUTINE",
        "clinician_notification": "PORTAL",
        "escalation": None,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# 6. load_validation_rules — bundle every rule set for the engine
# ─────────────────────────────────────────────────────────────────────────────
def load_validation_rules() -> Dict[str, Any]:
    """Return all Layer-5 validation rule sets for the lipid panel in one dict."""
    return {
        "severity_thresholds": SEVERITY_THRESHOLDS,
        "clinical_validation_rules": CLINICAL_VALIDATION_RULES,
        "impossible_conditions": IMPOSSIBLE_CONDITIONS,
        "confidence_calibration": CONFIDENCE_CALIBRATION,
        "urgency_flags": URGENCY_FLAGS,
    }


__all__ = [
    "SEVERITY_THRESHOLDS",
    "CLINICAL_VALIDATION_RULES",
    "IMPOSSIBLE_CONDITIONS",
    "CONFIDENCE_CALIBRATION",
    "URGENCY_FLAGS",
    "load_validation_rules",
]
