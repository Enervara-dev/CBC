"""
/api/analyze-url — adapter between the CBC pipeline and the ENERVERA main
application's LabProcessingService.extract() wire contract.

The main app POSTs a presigned document URL; this module defines the request
it sends and the response it expects back. The response deliberately mirrors
the main app's snake_case extraction contract exactly:

    { report_type, report_date, panels: [{ name, results: [
        { test_name, value, value_text, unit,
          reference_low, reference_high, reference_text, confidence } ]}],
      metadata: { lab_name, referring_doctor, collected_at,
                  accession_number, notes } }

The full CBC analysis (findings, flags, audit trail) rides along under
``analysis`` for debugging; the main app ignores unknown top-level fields, so
this changes nothing for callers that only read the extraction shape.

No clinical logic lives here — this is a pure projection of Layer-2
normalized biomarkers into the contract shape.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from domains.registry import (
    detect_panels,
    domain_for_code,
    get_domain,
    merged_code_to_name,
)


class AnalyzeUrlRequest(BaseModel):
    """What the main application sends."""

    document_url: str = Field(..., description="Presigned URL the service downloads the report from.")
    report_id: Optional[str] = Field(default=None, description="Main app's report id; used as patient_id.")
    user_id: Optional[str] = Field(default=None, description="Main app's user id.")
    mime_type: Optional[str] = Field(default=None, description="Declared MIME type (advisory only).")
    file_name: Optional[str] = Field(default=None, description="Original file name (for logs/audit).")
    request_id: Optional[str] = Field(default=None, description="Caller-supplied correlation id.")
    gender: Optional[str] = Field(default=None, description="'M'/'F' (synonyms accepted), for reference ranges.")
    age_years: Optional[int] = Field(default=None, ge=0, le=150, description="Patient age in years.")
    pregnant: Optional[bool] = Field(default=None, description="Applies pregnancy reference ranges.")
    include_reports: bool = Field(default=False, description="Also run Layer 6 LLM reports.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_url": "https://storage.example.com/report.pdf?signature=...",
                "report_id": "rpt-123",
                "user_id": "usr-456",
                "mime_type": "application/pdf",
                "file_name": "cbc-report.pdf",
                "request_id": "req-789",
            }
        }
    )


class AnalyzeUrlResponse(BaseModel):
    """The extraction contract, plus the full analysis under ``analysis``."""

    status: str
    report_type: str
    report_date: Optional[str] = None
    panels: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    error_messages: List[str] = Field(default_factory=list)
    analysis: Optional[Dict[str, Any]] = None
    # Layer 6 report bundle (only when include_reports=true was requested).
    reports: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "report_type": "CBC",
                "report_date": None,
                "panels": [{
                    "name": "Complete Blood Count",
                    "results": [{
                        "test_name": "Hemoglobin",
                        "value": 13.5,
                        "value_text": None,
                        "unit": "g/dL",
                        "reference_low": 12.0,
                        "reference_high": 16.0,
                        "reference_text": "12.0 - 16.0",
                        "confidence": 0.96,
                    }],
                }],
                "metadata": {
                    "lab_name": None, "referring_doctor": None,
                    "collected_at": None, "accession_number": None, "notes": None,
                },
                "error_messages": [],
                "analysis": {"status": "success"},
            }
        }
    )


def _empty_metadata() -> Dict[str, Any]:
    return {
        "lab_name": None,
        "referring_doctor": None,
        "collected_at": None,
        "accession_number": None,
        "notes": None,
    }


def _result_biomarkers(result: Any) -> List[Any]:
    return list(getattr(result, "normalized_biomarkers", None) or [])


def build_extraction_payload(result: Any) -> Dict[str, Any]:
    """
    Project a CBCAnalysisResult into the main app's extraction contract.

    Panels are grouped by registered domain; the dominant (primary) panel is
    listed first and gives the ``report_type``. Biomarkers whose code no
    domain recognises ride on the primary panel rather than being dropped.
    """
    biomarkers = _result_biomarkers(result)
    codes = [str(getattr(b, "biomarker_id", "")).strip().upper() for b in biomarkers]
    detection = detect_panels(codes)
    primary = detection.primary[0] if detection.primary else "cbc"
    code_to_name = merged_code_to_name()

    grouped: Dict[str, List[Any]] = {key: [] for key in detection.matched}
    grouped.setdefault(primary, [])
    for code, biomarker in zip(codes, biomarkers):
        key = domain_for_code(code) or primary
        grouped.setdefault(key, []).append((code, biomarker))

    panel_order = [primary] + sorted(k for k in grouped if k != primary)
    panels = []
    for key in panel_order:
        entries = sorted(grouped[key], key=lambda item: item[0])
        if not entries:
            continue
        results = []
        for code, b in entries:
            ref_min = getattr(b, "reference_min", None)
            ref_max = getattr(b, "reference_max", None)
            raw_name = getattr(b, "biomarker_name", None) or code_to_name.get(code, code)
            results.append({
                # Same display convention as the Layer-4 findings (rule_reasoner).
                "test_name": raw_name.replace("_", " ").title(),
                "value": getattr(b, "value", None),
                "value_text": None,
                "unit": getattr(b, "unit", None),
                "reference_low": ref_min,
                "reference_high": ref_max,
                "reference_text": (
                    f"{ref_min} - {ref_max}"
                    if ref_min is not None and ref_max is not None else None
                ),
                "confidence": getattr(b, "extraction_confidence", None),
            })
        try:
            panel_name = get_domain(key).name
        except KeyError:
            panel_name = key.upper()
        panels.append({"name": panel_name, "results": results})

    return {
        "status": result.status,
        "report_type": primary.upper(),
        "report_date": None,
        "panels": panels,
        "metadata": _empty_metadata(),
        "error_messages": list(result.error_messages),
        "analysis": result.to_dict(),
    }


def build_empty_payload(status: str, error_messages: List[str]) -> Dict[str, Any]:
    """The 'nothing readable' answer: a 200 with zero panels.

    The main app turns empty panels into its friendly NO_VALUES_FOUND message,
    which is the right UX for an unreadable report page.
    """
    return {
        "status": status,
        "report_type": "OTHER",
        "report_date": None,
        "panels": [],
        "metadata": _empty_metadata(),
        "error_messages": list(error_messages),
        "analysis": None,
    }
