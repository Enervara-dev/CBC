"""
CBC file analyzer — end-to-end pipeline from a PDF/image file to final findings.

    file  →  L1 OCR  →  Layer1→Layer2 adapter  →  CBCOrchestrator (L2→L5)
          →  CBCAnalysisResult (with Layer 1 threaded into the audit trail)

Layer 1 / adapter failures stop the pipeline (no biomarkers = nothing to do);
Layer 2–5 failures are handled inside the orchestrator (degrade to ``partial``).
"""

from __future__ import annotations

import inspect
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from orchestration.cbc_orchestrator import (
    CBCAnalysisResult,
    CBCOrchestrator,
    NestedAuditTrail,
)
from orchestration.layer1_adapter import Layer1ToLayer2Adapter

_SUPPORTED_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg")


class CBCFileAnalyzer:
    """Async pipeline: a report file in, a validated CBCAnalysisResult out."""

    def __init__(
        self,
        ocr_extractor: Any,
        layer2_normalizer: Any,
        layer3_feature_generator: Any,
        layer4_graph_reasoner: Any,
        layer5_validator: Any,
        adapter: Optional[Layer1ToLayer2Adapter] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.ocr_extractor = ocr_extractor
        self.adapter = adapter or Layer1ToLayer2Adapter()
        self.logger = logger or logging.getLogger(__name__)
        self.orchestrator = CBCOrchestrator(
            layer2_normalizer, layer3_feature_generator,
            layer4_graph_reasoner, layer5_validator, self.logger,
        )
        self.perf: Dict[str, float] = {}

    # ── Main pipeline ────────────────────────────────────────────────────────
    async def analyze_file(
        self,
        file_path: str,
        patient_id: str,
        gender: Optional[str] = None,
        age_years: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CBCAnalysisResult:
        """Run OCR → adapter → orchestrator for a report file."""
        run_start = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        metadata = metadata or {}

        # STEP 0 — validate file (hard precondition)
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Report file not found: {file_path}")
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in _SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file type {ext!r}; expected one of {_SUPPORTED_EXTENSIONS}."
            )
        file_type = "pdf" if ext == ".pdf" else "image"
        self.logger.info("File analysis start: %s (%s), patient=%s", file_path, file_type, patient_id)

        # STEP 1 — Layer 1: OCR extraction (fatal on failure)
        t0 = time.perf_counter()
        try:
            extracted = self.ocr_extractor.extract_biomarkers_from_file(file_path)
            ocr_rows: List[Dict[str, Any]] = await extracted if inspect.isawaitable(extracted) else extracted
            layer1_ms = self._ms(t0)
            self.logger.info("Layer 1 done in %.2fms: %d rows extracted", layer1_ms, len(ocr_rows))
            self.logger.debug("OCR rows: %s", ocr_rows)
        except Exception as exc:
            layer1_ms = self._ms(t0)
            self.logger.error("Layer 1 (OCR) failed: %s", exc)
            return self._error_result(patient_id, started_at, [f"layer1_ocr: {exc}"],
                                       file_path, file_type, 0, layer1_ms, run_start)

        # STEP 2 — adapt OCR rows → biomarker dict (fatal on failure)
        try:
            biomarker_dict, ocr_metadata = self.adapter.convert_ocr_rows_to_biomarker_dict(ocr_rows)
            self.logger.info("Layer 1 adapter done: %d biomarkers resolved", len(biomarker_dict))
        except Exception as exc:
            self.logger.error("Layer 1 adapter failed: %s", exc)
            return self._error_result(patient_id, started_at, [f"layer1_adapter: {exc}"],
                                       file_path, file_type, len(ocr_rows), layer1_ms, run_start)

        # STEP 3 — Layer 2→5 via the orchestrator
        result = await self.orchestrator.analyze_cbc(
            biomarker_values=biomarker_dict,
            patient_id=patient_id,
            metadata={**metadata, **ocr_metadata},
            gender=gender,
            age_years=age_years,
        )

        # Thread Layer 1 into the audit trail + total time
        result.audit_trail.layer1_ocr = {
            "file_path": file_path,
            "file_type": file_type,
            "ocr_rows_extracted": len(ocr_rows),
            "execution_ms": layer1_ms,
        }
        result.audit_trail.layer_timings["layer1_ms"] = layer1_ms
        result.audit_trail.total_execution_ms += layer1_ms
        result.execution_time_ms = self._ms(run_start)

        self.logger.info(
            "File analysis complete (status=%s, patient=%s, time=%.2fms, findings=%d)",
            result.status, patient_id, result.execution_time_ms, len(result.final_findings),
        )
        return result

    # ── Helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _ms(start: float) -> float:
        return round((time.perf_counter() - start) * 1000.0, 3)

    def _error_result(
        self,
        patient_id: str,
        started_at: str,
        errors: List[str],
        file_path: str,
        file_type: str,
        ocr_rows_extracted: int,
        layer1_ms: float,
        run_start: float,
    ) -> CBCAnalysisResult:
        """Terminal error result for Layer 1 / adapter failures."""
        audit = NestedAuditTrail(
            timestamp=datetime.now(timezone.utc).isoformat(),
            patient_id=patient_id,
            biomarker_input_count=0,
            layer2_normalization={}, layer3_features={},
            layer4_graph_reasoning={}, layer5_validation={},
            total_execution_ms=layer1_ms,
            layer_timings={"layer1_ms": layer1_ms},
            layer1_ocr={
                "file_path": file_path, "file_type": file_type,
                "ocr_rows_extracted": ocr_rows_extracted, "execution_ms": layer1_ms,
            },
        )
        return CBCAnalysisResult(
            patient_id=patient_id, timestamp=started_at, status="error",
            final_findings=[], clinical_flags={}, audit_trail=audit,
            error_messages=errors, execution_time_ms=self._ms(run_start),
        )
