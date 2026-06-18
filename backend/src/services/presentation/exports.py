"""
Deterministic exporters (Layer 6) — JSON / HL7 v2 / CSV / PDF metadata.

**No LLM involvement.** Every export is a pure, deterministic function of the
validated Layer-5 data: identical input → identical output. The LLM-written
report text is embedded verbatim where relevant (e.g. JSON), never re-reasoned.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Dict, List, Optional


def confidence_band(confidence: float) -> str:
    """>=0.80 high · 0.60–0.79 moderate · <0.60 uncertain (the spec's thresholds)."""
    try:
        c = float(confidence)
    except (TypeError, ValueError):
        return "uncertain"
    if c >= 0.80:
        return "high"
    if c >= 0.60:
        return "moderate"
    return "uncertain"


def _hl7_timestamp(timestamp: str) -> str:
    """ISO-8601 → HL7 ``YYYYMMDDHHMMSS`` (digits only, truncated to 14)."""
    digits = re.sub(r"\D", "", timestamp or "")
    return (digits[:14] or "00000000000000")


def _finding_fields(f: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize the fields used across exports from a FinalFinding dict."""
    conf = f.get("final_confidence", f.get("confidence", 0.0)) or 0.0
    return {
        "finding_id": f.get("finding_id", ""),
        "finding_name": f.get("finding_name", f.get("finding_id", "")),
        "confidence": conf,
        "band": confidence_band(conf),
        "severity": f.get("severity", ""),
        "status": f.get("status", ""),
        "validation_passed": f.get("validation_passed", ""),
        "evidence": list(f.get("source_evidence", []) or []),
    }


# ── JSON ─────────────────────────────────────────────────────────────────────
def to_json(
    *,
    patient_id: str,
    timestamp: str,
    status: str,
    final_findings: List[Dict[str, Any]],
    clinical_flags: Dict[str, List[Dict[str, Any]]],
    recommendations: List[Dict[str, Any]],
    patient_report: str = "",
    clinician_report: str = "",
) -> str:
    """Structured JSON export — validated data + the report texts, sorted keys."""
    payload = {
        "patient_id": patient_id,
        "timestamp": timestamp,
        "status": status,
        "final_findings": final_findings,
        "clinical_flags": clinical_flags,
        "recommendations": recommendations,
        "reports": {"patient": patient_report, "clinician": clinician_report},
    }
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


# ── HL7 v2 (ORU^R01) ─────────────────────────────────────────────────────────
def to_hl7_v2(
    *,
    patient_id: str,
    timestamp: str,
    final_findings: List[Dict[str, Any]],
) -> str:
    """
    Minimal, well-formed HL7 v2.5 ORU^R01: MSH + PID + OBR + one OBX per finding.
    Segments are separated by carriage returns (HL7 convention).
    """
    ts = _hl7_timestamp(timestamp)
    ctrl = f"ENV{ts}"
    segments = [
        f"MSH|^~\\&|ENERVERA|CBC|||{ts}||ORU^R01|{ctrl}|P|2.5",
        f"PID|1||{patient_id}",
        f"OBR|1|||CBC^Complete Blood Count Interpretation|||{ts}",
    ]
    for i, f in enumerate(final_findings, start=1):
        ff = _finding_fields(f)
        value = f"{ff['finding_name']} (confidence {ff['band']}; severity {ff['severity'] or 'n/a'})"
        # OBX|set|type|identifier|sub|value|units|range|abnormal-flags|...|status
        abnormal = "A" if str(ff["severity"]).lower() in ("urgent", "critical") else "N"
        segments.append(
            f"OBX|{i}|ST|{ff['finding_id']}^{ff['finding_name']}||"
            f"{value}|||{abnormal}|||F"
        )
    return "\r".join(segments)


# ── CSV ──────────────────────────────────────────────────────────────────────
_CSV_COLUMNS = [
    "finding_id", "finding_name", "confidence", "confidence_band",
    "severity", "status", "validation_passed", "evidence_count", "evidence",
]


def to_csv(*, final_findings: List[Dict[str, Any]]) -> str:
    """CSV export — one row per finding (uses the csv module for correct quoting)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_CSV_COLUMNS)
    for f in final_findings:
        ff = _finding_fields(f)
        writer.writerow([
            ff["finding_id"], ff["finding_name"], ff["confidence"], ff["band"],
            ff["severity"], ff["status"], ff["validation_passed"],
            len(ff["evidence"]), " | ".join(ff["evidence"]),
        ])
    return buf.getvalue()


# ── PDF metadata ─────────────────────────────────────────────────────────────
def to_pdf_metadata(
    *,
    patient_id: str,
    timestamp: str,
    status: str,
    final_findings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """PDF document-metadata dict (for a downstream PDF generator)."""
    names = [(_finding_fields(f)["finding_name"]) for f in final_findings]
    return {
        "Title": f"CBC Analysis Report — {patient_id}",
        "Author": "ENERVERA CBC Analysis System",
        "Subject": "Complete Blood Count interpretation",
        "Keywords": ", ".join(["CBC", "blood report", *names]) if names else "CBC, blood report",
        "Producer": "ENERVERA Layer 6 (deterministic export)",
        "Creator": "ENERVERA",
        "CreationDate": timestamp,
        "PatientID": patient_id,
        "Status": status,
        "FindingCount": len(final_findings),
    }


# ── Bundle ───────────────────────────────────────────────────────────────────
def build_exports(
    *,
    patient_id: str,
    timestamp: str,
    status: str,
    final_findings: List[Dict[str, Any]],
    clinical_flags: Dict[str, List[Dict[str, Any]]],
    recommendations: List[Dict[str, Any]],
    patient_report: str = "",
    clinician_report: str = "",
) -> Dict[str, Any]:
    """All four exports as ``{json, hl7_v2, csv, pdf_metadata}``."""
    return {
        "json": to_json(
            patient_id=patient_id, timestamp=timestamp, status=status,
            final_findings=final_findings, clinical_flags=clinical_flags,
            recommendations=recommendations, patient_report=patient_report,
            clinician_report=clinician_report,
        ),
        "hl7_v2": to_hl7_v2(patient_id=patient_id, timestamp=timestamp, final_findings=final_findings),
        "csv": to_csv(final_findings=final_findings),
        "pdf_metadata": to_pdf_metadata(
            patient_id=patient_id, timestamp=timestamp, status=status, final_findings=final_findings
        ),
    }
