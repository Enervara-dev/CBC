"""
Pydantic schemas — Layer 5 (Confidence Validation) input/output contracts.

Layer 5 takes the Layer 4 reasoning output, applies clinical validation rules +
impossibility checks, calibrates confidence, and assigns clinical urgency /
escalation — producing clinician-ready final findings and flags with a full
audit trail.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class FinalFinding(BaseModel):
    """A clinically-validated finding, ready for clinician review."""

    finding_id: str = Field(..., description="Finding key, e.g. 'iron_deficiency_anemia'.")
    finding_name: str = Field(..., description="Human-readable finding name.")
    final_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence after calibration, in [0, 1]."
    )
    severity: str = Field(..., description="'critical' / 'urgent' / 'routine' / 'normal'.")
    clinical_notes: str = Field(..., description="Explanation for the clinician.")
    validation_passed: bool = Field(..., description="Whether all required rules passed.")
    validation_rules_applied: List[str] = Field(
        default_factory=list, description="Ids of the validation rules that were applied/passed."
    )
    source_evidence: List[str] = Field(
        default_factory=list, description="Biomarker evidence links supporting the finding."
    )
    status: str = Field(
        ...,
        description="'APPROVED_FOR_REVIEW' / 'REQUIRES_MANUAL_REVIEW' / 'FLAGGED_FOR_ESCALATION'.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "finding_id": "iron_deficiency_anemia",
                "finding_name": "Iron Deficiency Anemia",
                "final_confidence": 0.86,
                "severity": "routine",
                "clinical_notes": "Microcytic anemia with high RDW; consistent with iron deficiency. "
                                  "Recommend iron studies (ferritin, TIBC).",
                "validation_passed": True,
                "validation_rules_applied": ["evidence_check", "consistency_check"],
                "source_evidence": ["HGB=10.2 (LOW) supports Iron Deficiency Anemia (strength: 0.95)"],
                "status": "APPROVED_FOR_REVIEW",
            }
        }
    )


class ClinicalFlag(BaseModel):
    """A clinical alert raised for a finding (by urgency level)."""

    flag_id: str = Field(..., description="Unique flag id.")
    flag_type: str = Field(..., description="'critical' / 'urgent' / 'routine'.")
    finding_id: str = Field(..., description="Finding this flag relates to.")
    message: str = Field(..., description="Human-readable alert message.")
    action_required: Optional[str] = Field(
        default=None, description="What the clinician should do."
    )
    escalation_level: Optional[str] = Field(
        default=None, description="e.g. 'hematology_consult', 'specialist_review'."
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "flag_id": "flag_hgb_critical_001",
                "flag_type": "critical",
                "finding_id": "iron_deficiency_anemia",
                "message": "Hemoglobin critically low (4.8 g/dL) — immediate attention required.",
                "action_required": "Notify clinician STAT; consider transfusion assessment.",
                "escalation_level": "hematology_consult",
            }
        }
    )


class ValidationAuditTrail(BaseModel):
    """Full traceability for one Layer 5 validation run."""

    timestamp: str = Field(..., description="ISO-8601 start timestamp.")
    layer4_input: Dict[str, Any] = Field(..., description="The Layer 4 output that was validated.")
    validation_checks_performed: List[str] = Field(
        default_factory=list, description="Names of the checks that ran."
    )
    rules_applied_count: int = Field(..., ge=0, description="Total validation rules applied.")
    findings_validated: int = Field(..., ge=0, description="Findings that passed validation.")
    impossible_conditions_flagged: List[str] = Field(
        default_factory=list, description="Impossible-condition rule ids that fired."
    )
    confidence_adjustments: Dict[str, Any] = Field(
        default_factory=dict, description="Per-finding original→final confidence."
    )
    clinician_approvals_required: int = Field(
        ..., ge=0, description="Findings needing clinician approval."
    )
    escalations_triggered: int = Field(..., ge=0, description="Escalations triggered.")
    execution_time_ms: float = Field(..., ge=0.0, description="Wall-clock duration in ms.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "timestamp": "2026-06-11T00:00:00+00:00",
                "layer4_input": {"validated_findings": 2, "conflicts": 0},
                "validation_checks_performed": [
                    "evidence_check", "impossible_check", "consistency_check", "severity_check",
                ],
                "rules_applied_count": 6,
                "findings_validated": 2,
                "impossible_conditions_flagged": [],
                "confidence_adjustments": {"iron_deficiency_anemia": {"original": 0.95, "final": 0.86}},
                "clinician_approvals_required": 1,
                "escalations_triggered": 0,
                "execution_time_ms": 12.4,
            }
        }
    )


class Layer5Output(BaseModel):
    """The complete Layer 5 result — clinician-ready."""

    final_findings: List[FinalFinding] = Field(
        default_factory=list, description="Clinically validated findings."
    )
    clinical_flags: Dict[str, List[ClinicalFlag]] = Field(
        default_factory=dict, description="Flags grouped by urgency level (critical/urgent/routine)."
    )
    audit_trail: ValidationAuditTrail = Field(..., description="Traceability for the run.")
    status: str = Field(..., description="'success' / 'partial' / 'requires_manual_review'.")
    clinician_review_required: bool = Field(
        ..., description="True if any finding needs manual clinician review."
    )
    next_steps: List[str] = Field(
        default_factory=list, description="Recommended actions for the clinician."
    )
    error_messages: List[str] = Field(
        default_factory=list, description="Per-step failures (status 'partial' if non-empty)."
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "final_findings": [FinalFinding.model_config["json_schema_extra"]["example"]],
                "clinical_flags": {
                    "critical": [ClinicalFlag.model_config["json_schema_extra"]["example"]],
                    "urgent": [],
                    "routine": [],
                },
                "audit_trail": ValidationAuditTrail.model_config["json_schema_extra"]["example"],
                "status": "success",
                "clinician_review_required": True,
                "next_steps": ["Review iron-deficiency finding", "Order iron studies"],
                "error_messages": [],
            }
        }
    )
