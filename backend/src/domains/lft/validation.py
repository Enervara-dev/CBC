"""
LFT clinical validation rules, severity thresholds, and confidence calibration
(Layer 5 knowledge base).

Same declarative shapes as ``domains/cbc/validation.py`` — the Layer-5 engine is
panel-agnostic and reads whatever the registry hands it, so the keys here must
match the CBC bundle's keys exactly.

Clinical references
-------------------
* Transaminase interpretation and the R factor: AASLD / ACG "Evaluation of
  Abnormal Liver Chemistries" (2017).
* Critical ("panic") values: CLSI GP47 and common laboratory policies —
  ALT/AST > 1000 U/L, total bilirubin > 15 mg/dL, albumin < 1.5 g/dL.
* Reference intervals: CLSI EP28-A3c (sex-stratified for the enzymes).
Values are conservative defaults and MUST be reviewed against the deploying
laboratory's authoritative ranges before clinical use.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# ─────────────────────────────────────────────────────────────────────────────
# 1. SEVERITY_THRESHOLDS
#    canonical name -> severity band -> {"low": (lo, hi), "high": (lo, hi)} ;
#    plus a flat "normal" (lo, hi). Ranges are half-open [lo, hi).
# ─────────────────────────────────────────────────────────────────────────────
SEVERITY_THRESHOLDS: Dict[str, Dict[str, Any]] = {
    # ALT (U/L) — > 1000 is a panic value (acute hepatitis / toxic / ischaemic)
    "alt": {
        "critical": {"low": (0.0, 0.0), "high": (1000.0, 20000.0)},
        "urgent":   {"low": (0.0, 0.0), "high": (400.0, 1000.0)},
        "routine":  {"low": (0.0, 7.0), "high": (40.0, 400.0)},
        "normal":   (7.0, 40.0),
    },
    # AST (U/L)
    "ast": {
        "critical": {"low": (0.0, 0.0), "high": (1000.0, 20000.0)},
        "urgent":   {"low": (0.0, 0.0), "high": (400.0, 1000.0)},
        "routine":  {"low": (0.0, 9.0), "high": (40.0, 400.0)},
        "normal":   (9.0, 40.0),
    },
    # ALP (U/L) — marked elevation suggests obstruction / infiltration
    "alp": {
        "urgent":   {"low": (0.0, 0.0), "high": (1000.0, 5000.0)},
        "routine":  {"low": (0.0, 40.0), "high": (129.0, 1000.0)},
        "normal":   (40.0, 129.0),
    },
    # GGT (U/L) — no true panic value; nominal bands
    "ggt": {
        "routine":  {"low": (0.0, 8.0), "high": (61.0, 1000.0)},
        "normal":   (8.0, 61.0),
    },
    # Total bilirubin (mg/dL) — deep jaundice / liver failure workup above 15
    "total_bilirubin": {
        "critical": {"low": (0.0, 0.0), "high": (15.0, 60.0)},
        "urgent":   {"low": (0.0, 0.0), "high": (5.0, 15.0)},
        "routine":  {"low": (0.0, 0.2), "high": (1.2, 5.0)},
        "normal":   (0.2, 1.2),
    },
    # Direct bilirubin (mg/dL)
    "direct_bilirubin": {
        "urgent":   {"low": (0.0, 0.0), "high": (5.0, 40.0)},
        "routine":  {"low": (0.0, 0.0), "high": (0.3, 5.0)},
        "normal":   (0.0, 0.3),
    },
    # Indirect bilirubin (mg/dL)
    "indirect_bilirubin": {
        "routine":  {"low": (0.0, 0.1), "high": (0.9, 40.0)},
        "normal":   (0.1, 0.9),
    },
    # Albumin (g/dL) — severe hypoalbuminaemia is a panic value
    "albumin": {
        "critical": {"low": (0.0, 1.5), "high": (7.0, 10.0)},
        "urgent":   {"low": (1.5, 2.5), "high": (5.5, 7.0)},
        "routine":  {"low": (2.5, 3.5), "high": (5.2, 5.5)},
        "normal":   (3.5, 5.2),
    },
    # Total protein (g/dL)
    "total_protein": {
        "routine":  {"low": (3.0, 6.0), "high": (8.3, 12.0)},
        "normal":   (6.0, 8.3),
    },
    # Globulin (g/dL)
    "globulin": {
        "routine":  {"low": (0.5, 2.0), "high": (3.5, 8.0)},
        "normal":   (2.0, 3.5),
    },
    # A/G ratio (unitless) — reversal (< 1) is the clinically used side
    "ag_ratio": {
        "routine":  {"low": (0.0, 1.0), "high": (2.5, 10.0)},
        "normal":   (1.0, 2.5),
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# 2. CLINICAL_VALIDATION_RULES
#    finding -> evidence requirements + thresholds for the finding to validate.
#    Finding ids use Layer-3 binary feature ids (e.g. "alt_high").
# ─────────────────────────────────────────────────────────────────────────────
CLINICAL_VALIDATION_RULES: Dict[str, Dict[str, Any]] = {
    # Pattern (ACG 2017): R = (ALT/ULN) / (ALP/ULN). r_factor_high is R > 5
    # (hepatocellular), r_factor_low is R < 2 (cholestatic).
    "hepatocellular_injury": {
        "required_findings": ["alt_high"],
        "optional_findings": ["ast_high", "total_bilirubin_high"],
        "contradictory_findings": ["alt_low", "r_factor_low"],
        "min_confidence": 0.72,
        "severity_factor": 0.9,
    },
    "cholestasis": {
        # ALP >= 1.5 x ULN; a normal GGT means bone, not bile ducts.
        "required_findings": ["alp_high", "alp_xuln_high"],
        "optional_findings": ["ggt_high", "direct_bilirubin_high"],
        "contradictory_findings": ["alp_low", "r_factor_high", "ggt_normal"],
        "min_confidence": 0.72,
        "severity_factor": 0.9,
    },
    "isolated_alp_elevation": {
        "required_findings": ["alp_high", "ggt_normal"],
        "optional_findings": [],
        "contradictory_findings": ["ggt_high", "direct_bilirubin_high"],
        "min_confidence": 0.70,
        "severity_factor": 1.0,
    },
    "biliary_obstruction": {
        "required_findings": ["alp_xuln_high", "direct_bilirubin_high", "total_bilirubin_high"],
        "optional_findings": ["ggt_high", "conjugated_bilirubin_fraction_high"],
        "contradictory_findings": ["alp_low", "r_factor_high", "ggt_normal",
                                   "conjugated_bilirubin_fraction_low"],
        "min_confidence": 0.75,
        "severity_factor": 0.9,
    },
    "alcoholic_liver_disease": {
        # AST/ALT > 2 is the pattern itself, not optional colour.
        "required_findings": ["ast_high", "de_ritis_ratio_high"],
        "optional_findings": ["ggt_high", "alt_high"],
        "contradictory_findings": ["ast_low"],
        "min_confidence": 0.70,
        "severity_factor": 0.9,
    },
    "viral_hepatitis": {
        "required_findings": ["alt_high", "ast_high"],
        "optional_findings": ["total_bilirubin_high", "direct_bilirubin_high"],
        "contradictory_findings": ["alt_low", "r_factor_low"],
        "min_confidence": 0.70,
        "severity_factor": 0.9,
    },
    "nafld_suspected": {
        "required_findings": ["alt_high"],
        "optional_findings": ["ggt_high"],
        "contradictory_findings": ["total_bilirubin_high", "r_factor_low"],
        "min_confidence": 0.65,
        "severity_factor": 0.95,
    },
    "hepatic_synthetic_dysfunction": {
        "required_findings": ["albumin_low"],
        "optional_findings": ["ag_ratio_low", "globulin_high", "total_bilirubin_high"],
        "contradictory_findings": ["albumin_high"],
        "min_confidence": 0.70,
        "severity_factor": 1.0,
    },
    "cirrhosis_suspected": {
        "required_findings": ["albumin_low", "ag_ratio_low"],
        "optional_findings": ["globulin_high", "total_bilirubin_high", "ast_high"],
        "contradictory_findings": ["albumin_high"],
        "min_confidence": 0.75,
        "severity_factor": 1.0,
    },
    # Unconjugated = conjugated fraction below 20% of total, whatever the direct
    # value is against its own narrow limit.
    "unconjugated_hyperbilirubinemia": {
        "required_findings": ["indirect_bilirubin_high", "conjugated_bilirubin_fraction_low"],
        "optional_findings": ["total_bilirubin_high"],
        "contradictory_findings": ["conjugated_bilirubin_fraction_high"],
        "min_confidence": 0.70,
        "severity_factor": 0.95,
    },
    "gilbert_syndrome": {
        "required_findings": ["indirect_bilirubin_high", "conjugated_bilirubin_fraction_low"],
        # Gilbert is a diagnosis of normal enzymes: each one measured normal is evidence.
        "optional_findings": ["alt_normal", "ast_normal", "alp_normal", "ggt_normal",
                              "direct_bilirubin_normal"],
        # Anaemia with unconjugated bilirubin is haemolysis until shown otherwise.
        "contradictory_findings": ["alt_high", "ast_high", "alp_high", "ggt_high",
                                   "hemoglobin_low"],
        "min_confidence": 0.68,
        "severity_factor": 1.0,
    },
    # Cross-panel: anaemia with a predominantly unconjugated bilirubin.
    "haemolysis_suspected": {
        "required_findings": ["hemoglobin_low", "indirect_bilirubin_high",
                              "conjugated_bilirubin_fraction_low"],
        "optional_findings": ["rdw_high", "ast_high", "mcv_high"],
        "contradictory_findings": [],
        "min_confidence": 0.72,
        "severity_factor": 1.0,
    },
    "acute_liver_failure_risk": {
        # ALT >= 10 x ULN (severity gate) with bilirubin > 2 x ULN.
        "required_findings": ["alt_high", "total_bilirubin_xuln_high"],
        "optional_findings": ["ast_high", "albumin_low"],
        "contradictory_findings": [],
        "min_confidence": 0.80,
        "severity_factor": 0.85,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# 3. IMPOSSIBLE_CONDITIONS
#    Mutually-exclusive findings that cannot coexist → flag a conflict.
# ─────────────────────────────────────────────────────────────────────────────
IMPOSSIBLE_CONDITIONS: Dict[str, Dict[str, Any]] = {
    "impossible_alt_high_and_low": {
        "conditions": ["alt_high", "alt_low"],
        "action": "FLAG_CONFLICT",
        "severity": "high",
    },
    "impossible_ast_high_and_low": {
        "conditions": ["ast_high", "ast_low"],
        "action": "FLAG_CONFLICT",
        "severity": "high",
    },
    "impossible_alp_high_and_low": {
        "conditions": ["alp_high", "alp_low"],
        "action": "FLAG_CONFLICT",
        "severity": "high",
    },
    "impossible_albumin_high_and_low": {
        "conditions": ["albumin_low", "albumin_high"],
        "action": "FLAG_CONFLICT",
        "severity": "high",
    },
    "impossible_ag_ratio_high_and_low": {
        "conditions": ["ag_ratio_low", "ag_ratio_high"],
        "action": "FLAG_CONFLICT",
        "severity": "high",
    },
    # Gilbert is a diagnosis of exclusion: enzyme derangement rules it out.
    "impossible_gilbert_with_enzyme_derangement": {
        "conditions": ["gilbert_syndrome", "hepatocellular_injury"],
        "action": "FLAG_CONFLICT",
        "severity": "medium",
    },
    # Bilirubin cannot be predominantly conjugated and unconjugated at once.
    "impossible_conjugated_and_unconjugated_predominance": {
        "conditions": ["unconjugated_hyperbilirubinemia", "biliary_obstruction"],
        "action": "FLAG_CONFLICT",
        "severity": "medium",
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
        "urgency": "STAT",                       # immediate action required
        "clinician_notification": "EMAIL + SMS",
        "escalation": "HEPATOLOGY_CONSULT",
    },
    "urgent": {
        "urgency": "URGENT",                     # within 24 hours
        "clinician_notification": "EMAIL",
        "escalation": "GASTROENTEROLOGY_REVIEW",
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
    """Return all Layer-5 validation rule sets for the liver panel in one dict."""
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
