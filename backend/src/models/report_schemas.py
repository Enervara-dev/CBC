"""
Pydantic schemas — Layer 6 (LLM Presentation Engine) output contract.

The bundle carries the **validated, unmodified** Layer-5 structured data plus the
two LLM-written reports (presentation) and the deterministic exports
(JSON / HL7 / CSV / PDF metadata, generated without the LLM).
"""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field


class PatientReport(BaseModel):
    """Plain-English, patient-facing report (LLM presentation)."""

    audience: str = "patient"
    report_text: str = Field(..., description="The rendered patient report.")
    word_count: int = Field(..., ge=0)
    generated: bool = Field(True, description="False if LLM generation failed for this report.")


class ClinicianReport(BaseModel):
    """Detailed clinician-facing report (LLM presentation)."""

    audience: str = "clinician"
    report_text: str = Field(..., description="The rendered clinician report.")
    word_count: int = Field(..., ge=0)
    generated: bool = Field(True, description="False if LLM generation failed for this report.")


class ReportExports(BaseModel):
    """Deterministic exports built from validated Layer-5 data (no LLM)."""

    json_string: str = Field(..., description="JSON export string.")
    hl7_v2: str = Field(..., description="HL7 v2 ORU^R01 message.")
    csv: str = Field(..., description="CSV export (one row per finding).")
    pdf_metadata: Dict[str, Any] = Field(..., description="PDF document metadata.")


class ReportBundle(BaseModel):
    """The complete Layer 6 result."""

    patient_id: str
    timestamp: str = Field(..., description="ISO-8601 generation timestamp.")
    status: str = Field(..., description="'success' or 'partial' (an LLM report failed).")
    # validated Layer-5 data, unmodified
    final_findings: List[Dict[str, Any]] = Field(default_factory=list)
    clinical_flags: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    recommendations: List[Dict[str, Any]] = Field(default_factory=list)
    # presentation
    patient_report: PatientReport
    clinician_report: ClinicianReport
    # deterministic exports
    exports: ReportExports
    warnings: List[str] = Field(default_factory=list)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "patient_id": "patient_001",
                "timestamp": "2026-06-17T00:00:00+00:00",
                "status": "success",
                "final_findings": [{"finding_id": "iron_deficiency_anemia", "final_confidence": 0.86}],
                "clinical_flags": {"critical": [], "urgent": [], "routine": []},
                "recommendations": [{"recommendation_name": "Iron studies"}],
                "patient_report": {"audience": "patient", "report_text": "...", "word_count": 320, "generated": True},
                "clinician_report": {"audience": "clinician", "report_text": "...", "word_count": 900, "generated": True},
                "exports": {"json_string": "{...}", "hl7_v2": "MSH|...", "csv": "finding_id,...", "pdf_metadata": {}},
                "warnings": [],
            }
        }
    )
