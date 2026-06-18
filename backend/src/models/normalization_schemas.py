"""
Pydantic schemas — Layer 2 request/response contracts.

These models are the typed boundary for the normalization API: incoming
extracted biomarkers + patient metadata, and the outgoing normalized result.
They are distinct from the internal service dataclass
(``services.normalization.normalizer.NormalizedBiomarker``): this module is the
*API contract* (with validation + JSON-schema examples), the dataclass is the
in-process value object.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BiomarkerStatus(str, Enum):
    """Classification of a biomarker value against its reference interval."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL_LOW = "CRITICAL_LOW"
    CRITICAL_HIGH = "CRITICAL_HIGH"


class NormalizedBiomarker(BaseModel):
    """A single biomarker after normalization (API response item)."""

    biomarker_id: str = Field(..., description="Canonical biomarker code, e.g. 'HGB'.")
    biomarker_name: str = Field(..., description="Human-readable name, e.g. 'Hemoglobin'.")
    value: float = Field(..., description="Value converted to the standard unit.")
    unit: str = Field(..., description="Standard unit, e.g. 'g/dL'.")
    reference_min: float = Field(..., description="Lower bound of the reference interval.")
    reference_max: float = Field(..., description="Upper bound of the reference interval.")
    gender: str = Field(..., description="Patient gender used for the lookup ('M'/'F').")
    age_group: str = Field(..., description="'pediatric' / 'adult' / 'elderly'.")
    lab_source: str = Field(..., description="Lab whose range was applied ('Any' if generic).")
    status: BiomarkerStatus = Field(..., description="LOW / NORMAL / HIGH / CRITICAL_LOW / CRITICAL_HIGH.")
    deviation_from_min: float = Field(..., description="value - reference_min (standard units).")
    deviation_percent: float = Field(..., description="(value - reference_min) / reference_min * 100.")
    extraction_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Layer-1 OCR confidence in [0, 1]."
    )
    quality_issues: List[str] = Field(
        default_factory=list, description="Messages from the data-quality checker."
    )
    critical_flag: bool = Field(
        default=False, description="True if a panic / physiologically-impossible value was detected."
    )
    loinc_code: Optional[str] = Field(
        default=None, description="LOINC code for the biomarker (joins to graph Biomarker nodes)."
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "biomarker_id": "HGB",
                "biomarker_name": "Hemoglobin",
                "value": 13.5,
                "unit": "g/dL",
                "reference_min": 12.0,
                "reference_max": 16.0,
                "gender": "F",
                "age_group": "adult",
                "lab_source": "Quest",
                "status": "NORMAL",
                "deviation_from_min": 1.5,
                "deviation_percent": 12.5,
                "extraction_confidence": 0.96,
                "quality_issues": [],
                "critical_flag": False,
            }
        }
    )


class ExtractedBiomarker(BaseModel):
    """A single biomarker as produced by Layer 1 (extraction)."""

    name: str = Field(..., description="Biomarker name/synonym as read from the report.")
    value: Union[str, float] = Field(..., description="Raw value (may be a string from OCR).")
    unit: str = Field(..., description="Unit as read from the report, e.g. 'g/dL', 'g/L'.")
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="OCR extraction confidence in [0, 1]."
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"name": "hemoglobin", "value": "10.0", "unit": "g/L", "confidence": 0.96}
        }
    )


class PatientMetadata(BaseModel):
    """Patient context driving reference-range selection."""

    gender: str = Field(..., description="Patient gender, 'M' or 'F'.")
    age: int = Field(..., ge=0, le=150, description="Patient age in years.")
    lab_source: str = Field(default="Any", description="Preferred issuing lab; 'Any' for generic.")
    condition: Optional[str] = Field(
        default=None, description="Physiological qualifier, e.g. 'pregnancy'."
    )

    @field_validator("gender")
    @classmethod
    def _normalize_gender(cls, value: str) -> str:
        """Accept case-insensitive 'M'/'F' (and common synonyms); reject others."""
        v = value.strip().upper()
        if v in ("MALE",):
            v = "M"
        elif v in ("FEMALE",):
            v = "F"
        if v not in ("M", "F"):
            raise ValueError("gender must be 'M' or 'F'")
        return v

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"gender": "F", "age": 45, "lab_source": "Quest", "condition": None}
        }
    )


class NormalizationRequest(BaseModel):
    """Incoming request: a batch of extracted biomarkers + patient metadata."""

    extracted_biomarkers: List[Dict[str, Any]] = Field(
        ..., description="List of extracted biomarkers ({name, value, unit, confidence})."
    )
    patient_metadata: Dict[str, Any] = Field(
        ..., description="Patient context: gender, age, lab_source (and optional condition)."
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "extracted_biomarkers": [
                    {"name": "hemoglobin", "value": "13.5", "unit": "g/dL", "confidence": 0.96}
                ],
                "patient_metadata": {"gender": "F", "age": 45, "lab_source": "Quest"},
            }
        }
    )


class NormalizationResult(BaseModel):
    """Outgoing result: normalized biomarkers + run metadata."""

    status: str = Field(..., description="Overall outcome: 'success' / 'partial' / 'failed'.")
    normalized_biomarkers: List[NormalizedBiomarker] = Field(
        default_factory=list, description="Successfully normalized biomarkers."
    )
    total_extracted: int = Field(..., ge=0, description="Number of biomarkers received.")
    total_normalized: int = Field(..., ge=0, description="Number successfully normalized.")
    total_failed: int = Field(..., ge=0, description="Number that failed normalization.")
    validation_issues: List[str] = Field(
        default_factory=list, description="Per-biomarker failure messages."
    )
    critical_findings: List[str] = Field(
        default_factory=list, description="Critical / panic-value messages across the batch."
    )
    normalization_timestamp: str = Field(..., description="ISO-8601 timestamp of the run.")
    reference_ranges_version: str = Field(..., description="Version of the reference-range dataset used.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "normalized_biomarkers": [NormalizedBiomarker.model_config["json_schema_extra"]["example"]],
                "total_extracted": 1,
                "total_normalized": 1,
                "total_failed": 0,
                "validation_issues": [],
                "critical_findings": [],
                "normalization_timestamp": "2026-06-10T00:00:00+00:00",
                "reference_ranges_version": "1.0",
            }
        }
    )
