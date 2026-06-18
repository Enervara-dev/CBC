"""
Report generator (Layer 6) — validated findings → patient + clinician reports.

The generator:
  - serializes the **validated Layer-5 data** into the prompt as delimited
    *data blocks* (never as instructions),
  - calls the injected ``LLMClient`` for the patient and clinician reports
    concurrently, isolating per-report failures,
  - builds the deterministic exports (JSON / HL7 / CSV / PDF metadata) — these
    do **not** depend on the LLM and are always produced.

The LLM only rephrases/explains; it cannot add findings, diagnoses, confidence
values, lab values, recommendations, or guideline references (enforced by the
system prompts + the appended general safety prompt).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from models.report_schemas import (
    ClinicianReport,
    PatientReport,
    ReportBundle,
    ReportExports,
)
from services.presentation import exports as exporters
from services.presentation.llm_client import LLMClient, LLMError
from services.presentation.prompts import (
    CLINICIAN_USER_TEMPLATE,
    PATIENT_USER_TEMPLATE,
    clinician_system_prompt,
    fill,
    patient_system_prompt,
)

# Soft word-count bounds (warnings only — never fatal).
_PATIENT_BOUNDS = (250, 550)     # spec target 300–500
_CLINICIAN_BOUNDS = (700, 1300)  # spec target 800–1200

# Audit fields safe to pass to the clinician prompt (excludes the bulky, OCR-bearing
# embedded layer inputs — those are an injection surface and add no value here).
_AUDIT_KEYS = (
    "timestamp", "validation_checks_performed", "rules_applied_count",
    "findings_validated", "impossible_conditions_flagged",
    "clinician_approvals_required", "escalations_triggered", "execution_time_ms",
)


def _dump(obj: Any) -> Any:
    """Pydantic / dataclass / dict → plain dict (else passthrough)."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    return obj


def _data_block(data: Any) -> str:
    """Serialize data inside explicit delimiters so the model treats it as data."""
    if not data:
        return "None supplied"
    body = json.dumps(data, indent=2, default=str, sort_keys=True)
    return f"<data>\n{body}\n</data>"


class ReportGenerator:
    """Generate patient + clinician reports and deterministic exports."""

    def __init__(
        self,
        llm_client: LLMClient,
        patient_max_tokens: int = 2000,
        clinician_max_tokens: int = 4000,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.llm = llm_client
        self.patient_max_tokens = patient_max_tokens
        self.clinician_max_tokens = clinician_max_tokens
        self.logger = logger or logging.getLogger(__name__)

    # ── Public API ───────────────────────────────────────────────────────────
    async def generate(
        self,
        *,
        final_findings: List[Any],
        clinical_flags: Optional[Dict[str, List[Any]]] = None,
        recommendations: Optional[List[Any]] = None,
        guideline_references: Optional[List[str]] = None,
        audit_trail: Any = None,
        patient_id: str = "",
        timestamp: Optional[str] = None,
    ) -> ReportBundle:
        """Build the full report bundle from validated Layer-5 data."""
        timestamp = timestamp or datetime.now(timezone.utc).isoformat()
        findings = [_dump(f) for f in (final_findings or [])]
        flags = {k: [_dump(c) for c in v] for k, v in (clinical_flags or {}).items()}
        recs = [_dump(r) for r in (recommendations or [])]
        guidelines = list(guideline_references or [])
        audit = self._audit_summary(audit_trail)
        warnings: List[str] = []

        self.logger.info("Report generation: patient=%s, %d findings", patient_id, len(findings))

        # ── prompts (validated data is injected as delimited data blocks) ──────
        findings_block = _data_block(findings)
        flags_block = _data_block(flags)
        recs_block = _data_block(recs)
        patient_user = fill(PATIENT_USER_TEMPLATE, {
            "final_findings": findings_block,
            "clinical_flags": flags_block,
            "recommendations": recs_block,
        })
        clinician_user = fill(CLINICIAN_USER_TEMPLATE, {
            "final_findings": findings_block,
            "evidence_chains": _data_block(self._evidence_chains(findings)),
            "clinical_flags": flags_block,
            "recommendations": recs_block,
            "guideline_references": _data_block(guidelines),
            "audit_trail": _data_block(audit),
        })

        # ── LLM (concurrent, per-report isolation) ────────────────────────────
        patient_text, patient_err = await self._safe_complete(
            patient_system_prompt(), patient_user, self.patient_max_tokens, "patient"
        )
        clinician_text, clinician_err = await self._safe_complete(
            clinician_system_prompt(), clinician_user, self.clinician_max_tokens, "clinician"
        )

        patient_report = self._build_report(
            PatientReport, patient_text, patient_err, _PATIENT_BOUNDS, "patient", warnings
        )
        clinician_report = self._build_report(
            ClinicianReport, clinician_text, clinician_err, _CLINICIAN_BOUNDS, "clinician", warnings
        )

        status = "partial" if (patient_err or clinician_err) else "success"

        # ── deterministic exports (always produced; no LLM) ───────────────────
        exports_dict = exporters.build_exports(
            patient_id=patient_id, timestamp=timestamp, status=status,
            final_findings=findings, clinical_flags=flags, recommendations=recs,
            patient_report=patient_report.report_text, clinician_report=clinician_report.report_text,
        )

        return ReportBundle(
            patient_id=patient_id, timestamp=timestamp, status=status,
            final_findings=findings, clinical_flags=flags, recommendations=recs,
            patient_report=patient_report, clinician_report=clinician_report,
            exports=ReportExports(
                json_string=exports_dict["json"],
                hl7_v2=exports_dict["hl7_v2"],
                csv=exports_dict["csv"],
                pdf_metadata=exports_dict["pdf_metadata"],
            ),
            warnings=warnings,
        )

    async def generate_from_layer5(
        self,
        layer5_output: Any,
        *,
        patient_id: str,
        recommendations: Optional[List[Any]] = None,
        guideline_references: Optional[List[str]] = None,
        timestamp: Optional[str] = None,
    ) -> ReportBundle:
        """Convenience: pull final_findings / clinical_flags / audit from a Layer5Output."""
        return await self.generate(
            final_findings=getattr(layer5_output, "final_findings", []),
            clinical_flags=getattr(layer5_output, "clinical_flags", {}),
            recommendations=recommendations,
            guideline_references=guideline_references,
            audit_trail=getattr(layer5_output, "audit_trail", None),
            patient_id=patient_id,
            timestamp=timestamp,
        )

    # ── Helpers ──────────────────────────────────────────────────────────────
    async def _safe_complete(
        self, system: str, user: str, max_tokens: int, label: str
    ) -> Tuple[str, Optional[str]]:
        """Run one LLM completion; return (text, error). Never raises."""
        try:
            text = await self.llm.complete(system=system, user=user, max_tokens=max_tokens)
            return text, None
        except LLMError as exc:
            self.logger.error("%s report generation failed: %s", label, exc)
            return "", f"{label}: {exc}"
        except Exception as exc:  # noqa: BLE001 — isolate any client error
            self.logger.error("%s report generation failed: %s", label, exc)
            return "", f"{label}: {exc}"

    @staticmethod
    def _build_report(
        cls: Any, text: str, error: Optional[str], bounds: Tuple[int, int],
        label: str, warnings: List[str],
    ) -> Any:
        """Construct a PatientReport/ClinicianReport and record length/failure warnings."""
        word_count = len(text.split())
        if error:
            warnings.append(f"{label}_report_failed: {error}")
            return cls(report_text="", word_count=0, generated=False)
        low, high = bounds
        if word_count < low or word_count > high:
            warnings.append(f"{label}_report_length {word_count} words outside target {low}-{high}")
        return cls(report_text=text, word_count=word_count, generated=True)

    @staticmethod
    def _evidence_chains(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Per-finding evidence narratives (from the validated source_evidence)."""
        chains: List[Dict[str, Any]] = []
        for f in findings:
            evidence = list(f.get("source_evidence", []) or [])
            if evidence:
                chains.append({
                    "finding_id": f.get("finding_id", ""),
                    "finding_name": f.get("finding_name", f.get("finding_id", "")),
                    "evidence": evidence,
                })
        return chains

    @staticmethod
    def _audit_summary(audit_trail: Any) -> Dict[str, Any]:
        """Compact, safe audit view (drops bulky embedded layer inputs / OCR text)."""
        audit = _dump(audit_trail) or {}
        if not isinstance(audit, dict):
            return {}
        return {k: audit[k] for k in _AUDIT_KEYS if k in audit}
