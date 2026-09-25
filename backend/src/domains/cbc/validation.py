"""
Clinical validation rules, severity thresholds, and confidence calibration
(Layer 5 knowledge base).

A declarative, auditable catalogue — WHAT must hold for a finding to be clinically
validated and HOW its confidence is calibrated. The engine that applies these
lives separately. All constants are designed to be lifted into an external config
(DB / YAML) later without changing the rule shapes.

Clinical references
-------------------
* Hemoglobin anaemia/severity bands: WHO "Haemoglobin concentrations for the
  diagnosis of anaemia" (2011/2022).
* WBC / platelet critical ("panic") values: CLSI GP47 and common lab critical-value
  policies (values vary by institution — treat as defaults, override per site).
* Reference intervals: CLSI EP28-A3c (sex/age-stratified).
Values here are conservative defaults and MUST be reviewed against the deploying
laboratory's authoritative ranges before clinical use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

# ─────────────────────────────────────────────────────────────────────────────
# 1. SEVERITY_THRESHOLDS
#    biomarker -> severity band -> {"low": (lo, hi), "high": (lo, hi)} ; plus a
#    flat "normal" (lo, hi). Ranges are half-open [lo, hi). Units in comments.
# ─────────────────────────────────────────────────────────────────────────────
SEVERITY_THRESHOLDS: Dict[str, Dict[str, Any]] = {
    # Hemoglobin (g/dL). WHO grades severe anaemia below 8 g/dL in non-pregnant
    # adults; below 7 is the restrictive transfusion threshold (AABB 2023), so it
    # is the critical band. The patient's own limit (sex, age, pregnancy) decides
    # whether a value is low at all — the reasoner floors out-of-range at routine.
    "hemoglobin": {
        "critical": {"low": (0.0, 7.0), "high": (20.0, 30.0)},
        "urgent":   {"low": (7.0, 8.0), "high": (18.5, 20.0)},
        "routine":  {"low": (8.0, 12.0), "high": (16.0, 18.5)},
        "normal":   (12.0, 16.0),
    },
    # WBC (K/uL) — a total below 1.0 is a common critical-call value and above 50
    # hyperleukocytosis. Infection risk itself is graded on the ANC below.
    "wbc": {
        "critical": {"low": (0.0, 1.0), "high": (50.0, 100.0)},
        "urgent":   {"low": (1.0, 2.0), "high": (30.0, 50.0)},
        "routine":  {"low": (2.0, 4.5), "high": (11.0, 30.0)},
        "normal":   (4.5, 11.0),
    },
    # Absolute neutrophil count (K/uL) — CTCAE v5: < 0.5 grade 4 (severe),
    # 0.5–1.0 grade 3, 1.0–1.5 grade 2.
    "absolute_neutrophils": {
        "critical": {"low": (0.0, 0.5), "high": (0.0, 0.0)},
        "urgent":   {"low": (0.5, 1.0), "high": (30.0, 100.0)},
        "routine":  {"low": (1.0, 2.0), "high": (7.5, 30.0)},
        "normal":   (2.0, 7.5),
    },
    # Absolute lymphocyte count (K/uL) — < 0.5 (CTCAE grade 3+) raises the risk of
    # opportunistic infection; > 30 is lymphoproliferative until shown otherwise.
    "absolute_lymphocytes": {
        "urgent":   {"low": (0.0, 0.5), "high": (30.0, 500.0)},
        "routine":  {"low": (0.5, 1.0), "high": (4.0, 30.0)},
        "normal":   (1.0, 4.0),
    },
    # Platelets (K/uL) — bleeding risk <20 critical, <50 with procedures/trauma
    "platelets": {
        "critical": {"low": (0.0, 20.0), "high": (1000.0, 3000.0)},
        "urgent":   {"low": (20.0, 50.0), "high": (700.0, 1000.0)},
        "routine":  {"low": (50.0, 150.0), "high": (400.0, 700.0)},
        "normal":   (150.0, 400.0),
    },
    # Hematocrit (%) — tracks haemoglobin (critical < 20 pairs with Hb < 7);
    # > 55 carries hyperviscosity risk
    "hematocrit": {
        "critical": {"low": (0.0, 20.0), "high": (60.0, 80.0)},
        "urgent":   {"low": (20.0, 25.0), "high": (55.0, 60.0)},
        "routine":  {"low": (25.0, 36.0), "high": (50.0, 55.0)},
        "normal":   (36.0, 50.0),
    },
    # RBC count (M/uL) — the count is graded through haemoglobin and never exceeds
    # routine on its own (a high count with a low MCV is thalassaemia trait).
    "rbc": {
        "routine":  {"low": (0.0, 4.2), "high": (5.9, 12.0)},
        "normal":   (4.2, 5.9),
    },
    # MCV (fL) — classifies an anaemia; there is no panic value, so never above
    # routine (an MCV of 66 in iron deficiency is not itself an emergency).
    "mcv": {
        "routine":  {"low": (0.0, 80.0), "high": (100.0, 200.0)},
        "normal":   (80.0, 100.0),
    },
    # MCH (pg) — nominal bands (no panic value)
    "mch": {
        "routine":  {"low": (15.0, 27.0), "high": (33.0, 45.0)},
        "normal":   (27.0, 33.0),
    },
    # MCHC (g/dL) — high suggests spherocytosis/artifact
    "mchc": {
        "routine":  {"low": (28.0, 32.0), "high": (36.0, 40.0)},
        "normal":   (32.0, 36.0),
    },
    # RDW (%) — anisocytosis; only the high side is clinically used
    "rdw": {
        "routine":  {"low": (0.0, 11.5), "high": (14.5, 30.0)},
        "normal":   (11.5, 14.5),
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# 2. CLINICAL_VALIDATION_RULES
#    finding -> evidence requirements + thresholds for the finding to validate.
#    Finding ids use Layer-3 binary feature ids (e.g. "hemoglobin_low").
# ─────────────────────────────────────────────────────────────────────────────
CLINICAL_VALIDATION_RULES: Dict[str, Dict[str, Any]] = {
    # ── Anaemia by red cell size ──────────────────────────────────────────────
    # The morphological reading always fires; a specific cause replaces it
    # (ClinicalCondition.supersedes) only when the indices discriminate.
    "iron_deficiency_anemia": {
        "required_findings": ["hemoglobin_low", "mcv_low", "rdw_high"],
        "optional_findings": ["mch_low", "mchc_low", "platelets_high"],
        # Mentzer < 13 (many small cells) points to thalassaemia trait instead.
        "contradictory_findings": ["mcv_high", "mentzer_index_low"],
        "min_confidence": 0.75,
        "severity_factor": 0.9,   # multiply confidence by this at moderate severity
    },
    "thalassemia_trait": {
        "required_findings": ["mcv_low", "mentzer_index_low"],
        "optional_findings": ["rbc_high", "mch_low", "rdw_normal"],
        # Raised RDW favours iron deficiency; the trait gives uniform small cells.
        "contradictory_findings": ["mcv_high", "rdw_high"],
        "min_confidence": 0.72,
        "severity_factor": 1.0,
    },
    "microcytic_anemia": {
        "required_findings": ["hemoglobin_low", "mcv_low"],
        "optional_findings": ["rdw_high"],
        "contradictory_findings": ["mcv_high"],
        "min_confidence": 0.70,
        "severity_factor": 0.9,
    },
    "macrocytic_anemia": {
        "required_findings": ["hemoglobin_low", "mcv_high"],
        "optional_findings": ["rdw_high"],
        "contradictory_findings": ["mcv_low"],
        "min_confidence": 0.70,
        "severity_factor": 0.9,
    },
    # Megaloblastic range: MCV > 1.1 x upper limit (about 110 fL).
    "vitamin_b12_deficiency": {
        "required_findings": ["hemoglobin_low", "mcv_xuln_high"],
        "optional_findings": ["rdw_high", "wbc_low", "platelets_low",
                              "absolute_neutrophils_low"],
        "contradictory_findings": ["mcv_low"],
        "min_confidence": 0.72,
        "severity_factor": 0.9,
    },
    "folate_deficiency": {
        "required_findings": ["hemoglobin_low", "mcv_xuln_high"],
        "optional_findings": ["rdw_high"],
        "contradictory_findings": ["mcv_low"],
        "min_confidence": 0.70,
        "severity_factor": 0.9,
    },
    "normocytic_anemia": {
        "required_findings": ["hemoglobin_low", "mcv_normal"],
        "optional_findings": [],
        "contradictory_findings": ["mcv_low", "mcv_high"],
        "min_confidence": 0.65,
        "severity_factor": 0.95,
    },
    "chronic_disease_anemia": {
        "required_findings": ["hemoglobin_low", "mcv_normal"],
        "optional_findings": ["rdw_normal"],
        "contradictory_findings": ["mcv_low", "mcv_high", "rdw_high"],
        "min_confidence": 0.65,
        "severity_factor": 0.95,
    },
    # ── White cells — graded on absolute counts ───────────────────────────────
    "acute_infection": {
        "required_findings": ["wbc_high", "absolute_neutrophils_high"],
        "optional_findings": ["neutrophils_high"],
        # A lymphocyte-driven leukocytosis is viral or lymphoproliferative.
        "contradictory_findings": ["wbc_low", "absolute_lymphocytes_high"],
        "min_confidence": 0.70,
        "severity_factor": 1.0,
    },
    "immune_compromise": {
        "required_findings": ["absolute_neutrophils_low"],
        "optional_findings": ["wbc_low", "absolute_lymphocytes_low"],
        "contradictory_findings": ["absolute_neutrophils_high"],
        "min_confidence": 0.70,
        "severity_factor": 1.0,
    },
    "marked_leukocytosis": {
        "required_findings": ["wbc_high"],
        "optional_findings": ["hemoglobin_low", "platelets_low", "absolute_lymphocytes_high"],
        "contradictory_findings": [],
        "min_confidence": 0.75,
        "severity_factor": 1.0,
    },
    # ── Platelets and multi-lineage ──────────────────────────────────────────
    "severe_thrombocytopenia": {
        "required_findings": ["platelets_low"],
        "optional_findings": [],
        "contradictory_findings": ["platelets_high"],
        "min_confidence": 0.75,
        "severity_factor": 1.0,
    },
    "pancytopenia": {
        "required_findings": ["hemoglobin_low", "wbc_low", "platelets_low"],
        "optional_findings": ["absolute_neutrophils_low", "mcv_high"],
        "contradictory_findings": [],
        "min_confidence": 0.75,
        "severity_factor": 1.0,
    },
    "polycythemia": {
        "required_findings": ["hemoglobin_high", "hematocrit_high"],
        "optional_findings": ["rbc_high", "wbc_high", "platelets_high"],
        "contradictory_findings": ["mcv_low"],
        "min_confidence": 0.72,
        "severity_factor": 1.0,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# 3. IMPOSSIBLE_CONDITIONS
#    Mutually-exclusive findings that cannot coexist → flag a conflict.
# ─────────────────────────────────────────────────────────────────────────────
IMPOSSIBLE_CONDITIONS: Dict[str, Dict[str, Any]] = {
    "impossible_microcytic_and_macrocytic": {
        "conditions": ["microcytic_anemia", "macrocytic_anemia"],
        "action": "FLAG_CONFLICT",
        "severity": "high",
    },
    "impossible_high_and_low_hemoglobin": {
        "conditions": ["hemoglobin_low", "hemoglobin_high"],
        "action": "FLAG_CONFLICT",
        "severity": "high",
    },
    "impossible_mcv_low_and_high": {
        "conditions": ["mcv_low", "mcv_high"],
        "action": "FLAG_CONFLICT",
        "severity": "high",
    },
    "impossible_wbc_high_and_low": {
        "conditions": ["wbc_low", "wbc_high"],
        "action": "FLAG_CONFLICT",
        "severity": "high",
    },
    "impossible_platelets_high_and_low": {
        "conditions": ["platelets_low", "platelets_high"],
        "action": "FLAG_CONFLICT",
        "severity": "high",
    },
    "impossible_infection_and_immunosuppression": {
        "conditions": ["acute_infection", "immune_compromise"],
        "action": "FLAG_CONFLICT",
        "severity": "medium",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# 4. CONFIDENCE_CALIBRATION
#    Multipliers applied to a finding's confidence based on evidence quality.
# ─────────────────────────────────────────────────────────────────────────────
CONFIDENCE_CALIBRATION: Dict[str, Dict[str, float]] = {
    # Higher-severity findings get MORE scrutiny → lower multiplier. Applied to
    # every finding (not hemoglobin-specific); bands follow WHO-style grading.
    "severity_factor": {
        "critical": 0.85,
        "urgent": 0.90,
        "routine": 0.95,
        "normal": 1.0,
    },
    # More independent biomarker evidence → higher confidence.
    "evidence_count_factor": {
        "1_evidence": 0.70,
        "2_evidence": 0.85,
        "3_or_more_evidence": 0.95,
    },
    # Internal consistency of the evidence.
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
        "escalation": "HEMATOLOGY_CONSULT",
    },
    "urgent": {
        "urgency": "URGENT",                     # within 24 hours
        "clinician_notification": "EMAIL",
        "escalation": "SPECIALIST_REVIEW",
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
# 6. ClinicalRule dataclass — one evaluated validation rule (engine output unit)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ClinicalRule:
    """
    The result/representation of one applied clinical validation rule.

    Attributes
    ----------
    rule_id : str            unique rule id
    rule_name : str          human-readable name
    validation_type : str    EVIDENCE_CHECK / IMPOSSIBLE_CHECK / CONSISTENCY_CHECK / SEVERITY_CHECK
    action : str             PASS / FAIL / FLAG / ESCALATE
    reason : str             explanation (audit trail)
    severity : Optional[str] critical / urgent / routine / high / medium / low (if applicable)
    """

    rule_id: str
    rule_name: str
    validation_type: str
    action: str
    reason: str
    severity: Optional[str] = None


VALIDATION_TYPES = ("EVIDENCE_CHECK", "IMPOSSIBLE_CHECK", "CONSISTENCY_CHECK", "SEVERITY_CHECK")
RULE_ACTIONS = ("PASS", "FAIL", "FLAG", "ESCALATE")


# ─────────────────────────────────────────────────────────────────────────────
# 7. load_validation_rules — bundle every rule set for the engine
# ─────────────────────────────────────────────────────────────────────────────
def load_validation_rules() -> Dict[str, Any]:
    """
    Return all Layer-5 validation rule sets in one dict.

    Returns
    -------
    Dict[str, Any]
        ``{severity_thresholds, clinical_validation_rules, impossible_conditions,
           confidence_calibration, urgency_flags}``.
    """
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
    "ClinicalRule",
    "VALIDATION_TYPES",
    "RULE_ACTIONS",
    "load_validation_rules",
]
