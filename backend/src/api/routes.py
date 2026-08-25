"""
HTTP routes for the CBC analysis pipeline.

Exposes ``CBCOrchestrator.analyze_cbc()`` (Layers 2→5) as ``POST /api/analyze``:
raw biomarker values in, clinician-ready findings + clinical flags + audit out.

Dependency injection
--------------------
The orchestrator is expensive to build (it owns the Neo4j connection, the DB
session, and the rule sets), so it is constructed once at app startup and
registered via :func:`init_orchestrator`. Handlers receive it through the
:func:`get_orchestrator` dependency, which 503s if startup did not complete.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

import os
import tempfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from api.auth import api_key_scheme
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestration.cbc_orchestrator import CBCAnalysisResult, CBCOrchestrator
from orchestration.layer1_adapter import Layer1ToLayer2Adapter
from orchestration.ocr_space_client import OCRSpaceClient, OCRSpaceError
from services.presentation.report_generator import ReportGenerator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["analysis"])

# Smallest panel we will attempt to analyse (the orchestrator additionally
# enforces the specific REQUIRED_BIOMARKERS set and reports it in the result).
_MIN_BIOMARKERS = 3


# ─────────────────────────────────────────────────────────────────────────────
# Request / response schemas
# ─────────────────────────────────────────────────────────────────────────────
class AnalyzeCBCRequest(BaseModel):
    """Raw CBC panel for one patient."""

    patient_id: str = Field(..., description="Caller-defined patient identifier.")
    biomarker_values: Dict[str, float] = Field(
        ..., description='Canonical code → value, e.g. {"HGB": 10.2, "MCV": 75}.'
    )
    gender: Optional[str] = Field(None, description='Patient gender, "M" or "F".')
    age_years: Optional[int] = Field(None, ge=0, le=150, description="Patient age in years.")
    metadata: Optional[Dict[str, Any]] = Field(
        None, description='Optional context, e.g. {"lab_date": "2024-01-15"}.'
    )
    include_reports: bool = Field(
        False,
        description="If true, also generate Layer 6 patient/clinician reports + "
                    "exports (requires GEMINI_API_KEY; adds LLM latency).",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "patient_id": "patient_001",
                "biomarker_values": {
                    "HGB": 10.2, "MCV": 75, "RDW": 16.5, "WBC": 5.2, "PLT": 250,
                },
                "gender": "F",
                "age_years": 35,
                "metadata": {"lab_date": "2024-01-15"},
            }
        }
    )


class AnalyzeCBCResponse(BaseModel):
    """The end-to-end analysis result (mirrors :class:`CBCAnalysisResult`)."""

    patient_id: str
    timestamp: str = Field(..., description="ISO-8601 start time of the analysis.")
    status: str = Field(..., description='"success", "partial", or "error".')
    final_findings: List[Dict[str, Any]] = Field(default_factory=list)
    clinical_flags: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    recommendations: List[Dict[str, Any]] = Field(
        default_factory=list, description="Layer 4 recommendations (actions + follow-up tests)."
    )
    reports: Optional[Dict[str, Any]] = Field(
        None,
        description="Layer 6 report bundle (patient/clinician reports + JSON/HL7/CSV/PDF "
                    "exports). Present only when include_reports=true was requested.",
    )
    audit_trail: Dict[str, Any] = Field(default_factory=dict)
    error_messages: List[str] = Field(default_factory=list)
    execution_time_ms: float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Dependency injection
#
# The orchestrator + its stateless layers (L3/L4/L5) are app-lifetime singletons.
# Layer 2 needs a DB session, which must be *per-request* (an AsyncSession is not
# safe to share across concurrent requests), so we register a session factory and
# a normalizer factory at startup and build a fresh normalizer for each request.
# ─────────────────────────────────────────────────────────────────────────────
_orchestrator: Optional[CBCOrchestrator] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None
_normalizer_factory: Optional[Callable[[AsyncSession], Any]] = None
_report_generator: Optional[ReportGenerator] = None


def init_orchestrator(orchestrator: CBCOrchestrator) -> None:
    """Register the application-wide orchestrator (called once from startup)."""
    global _orchestrator
    _orchestrator = orchestrator
    logger.info("Orchestrator registered with API routes.")


def init_report_generator(generator: Optional[ReportGenerator]) -> None:
    """Register the Layer-6 report generator (or ``None`` if unavailable)."""
    global _report_generator
    _report_generator = generator
    logger.info("Report generator registered with API routes (available=%s).", generator is not None)


def init_db(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    """Register the async session factory used by :func:`get_db` (from startup)."""
    global _sessionmaker
    _sessionmaker = sessionmaker
    logger.info("Async session factory registered with API routes.")


def init_normalizer_factory(factory: Callable[[AsyncSession], Any]) -> None:
    """Register the per-request Layer-2 normalizer factory (from startup)."""
    global _normalizer_factory
    _normalizer_factory = factory
    logger.info("Normalizer factory registered with API routes.")


def get_orchestrator() -> CBCOrchestrator:
    """FastAPI dependency: return the orchestrator or 503 if not initialized."""
    if _orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized.")
    return _orchestrator


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield a fresh AsyncSession per request, then close it."""
    if _sessionmaker is None:
        raise HTTPException(status_code=503, detail="Database not initialized.")
    async with _sessionmaker() as session:
        yield session


async def _build_reports(
    result: CBCAnalysisResult, include_reports: bool
) -> Optional[Dict[str, Any]]:
    """
    Layer 6: build the report bundle for ``result`` when requested.

    Returns ``None`` when reports were not requested. Otherwise always returns a
    dict — a small status object when Layer 6 is unavailable or there is nothing
    to report — so failures never break the analysis response.
    """
    if not include_reports:
        return None
    if _report_generator is None:
        return {"status": "unavailable",
                "warnings": ["Layer 6 not configured (set GEMINI_API_KEY to enable reports)."]}
    if not result.final_findings:
        return {"status": "skipped", "warnings": ["No findings to report."]}
    try:
        bundle = await _report_generator.generate(
            final_findings=result.final_findings,
            clinical_flags=result.clinical_flags,
            recommendations=result.recommendations,
            audit_trail=result.audit_trail.layer5_validation,
            patient_id=result.patient_id,
            timestamp=result.timestamp,
        )
        return bundle.model_dump()
    except Exception as exc:  # noqa: BLE001 — never fail the analysis over report gen
        logger.exception("Layer 6 report generation failed for patient=%s", result.patient_id)
        return {"status": "error", "warnings": [f"report_generation_failed: {exc}"]}


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/analyze", response_model=AnalyzeCBCResponse, dependencies=[Depends(api_key_scheme)])
async def analyze_cbc(
    request: AnalyzeCBCRequest,
    db: AsyncSession = Depends(get_db),
    orchestrator: CBCOrchestrator = Depends(get_orchestrator),
) -> AnalyzeCBCResponse:
    """
    Run the full CBC analysis (Layers 2→5) for one patient's biomarker panel.

    HTTP semantics
    --------------
    - 200: analysis ran (``status`` may be ``success`` / ``partial`` / ``error``;
      a graceful, structured ``error`` — e.g. missing required biomarkers — still
      returns 200 with ``error_messages`` and the audit trail).
    - 400: malformed request (empty panel, or fewer than 3 biomarkers).
    - 503: orchestrator not initialized.
    - 500: unexpected server fault.
    """
    # STEP 1 — validate input
    if not request.biomarker_values:
        raise HTTPException(status_code=400, detail="biomarker_values must not be empty.")
    if len(request.biomarker_values) < _MIN_BIOMARKERS:
        raise HTTPException(
            status_code=400,
            detail=f"At least {_MIN_BIOMARKERS} biomarkers are required; "
                   f"got {len(request.biomarker_values)}.",
        )

    # STEP 2 — log incoming request
    logger.info(
        "POST /api/analyze: patient=%s, biomarkers=%d, gender=%s, age=%s",
        request.patient_id, len(request.biomarker_values), request.gender, request.age_years,
    )

    # STEP 3 — build a per-request Layer-2 normalizer (fresh DB session) + run
    if _normalizer_factory is None:
        raise HTTPException(status_code=503, detail="Normalizer factory not initialized.")
    normalizer = _normalizer_factory(db)
    try:
        result: CBCAnalysisResult = await orchestrator.analyze_cbc(
            biomarker_values=request.biomarker_values,
            patient_id=request.patient_id,
            metadata=request.metadata,
            gender=request.gender,
            age_years=request.age_years,
            normalizer=normalizer,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        logger.error("Bad request for patient=%s: %s", request.patient_id, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — convert any layer fault to a 500
        logger.exception("Unexpected error analysing patient=%s", request.patient_id)
        raise HTTPException(status_code=500, detail="Internal analysis error.") from exc

    # STEP 4 — log result
    flag_count = sum(len(v) for v in result.clinical_flags.values())
    logger.info(
        "Analysis complete: patient=%s, status=%s, findings=%d, flags=%d, time=%.2fms",
        result.patient_id, result.status, len(result.final_findings),
        flag_count, result.execution_time_ms,
    )

    # STEP 5 — Layer 6 reports (opt-in) + serialize
    reports = await _build_reports(result, request.include_reports)
    serialized = result.to_dict()
    return AnalyzeCBCResponse(
        patient_id=result.patient_id,
        timestamp=result.timestamp,
        status=result.status,
        final_findings=serialized["final_findings"],
        clinical_flags=serialized["clinical_flags"],
        recommendations=serialized["recommendations"],
        reports=reports,
        audit_trail=serialized["audit_trail"],
        error_messages=result.error_messages,
        execution_time_ms=result.execution_time_ms,
    )


_SUPPORTED_UPLOAD_EXT = (".pdf", ".png", ".jpg", ".jpeg")


@router.post("/analyze-file", response_model=AnalyzeCBCResponse, dependencies=[Depends(api_key_scheme)])
async def analyze_file(
    file: UploadFile = File(..., description="CBC report (PDF or image)."),
    patient_id: str = Form(...),
    gender: Optional[str] = Form(None),
    age_years: Optional[int] = Form(None),
    include_reports: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    orchestrator: CBCOrchestrator = Depends(get_orchestrator),
) -> AnalyzeCBCResponse:
    """
    End-to-end analysis from a report file.

    The file is sent to the OCR.space API (Layer 1) for text extraction; the
    parsed rows are mapped to canonical biomarker codes (Layer 1→2 adapter) and
    run through the same orchestrator as ``/analyze``.

    HTTP semantics mirror ``/analyze``; additionally returns 502 if the OCR
    service is unreachable/errors and 422 if no biomarkers can be extracted.
    """
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _SUPPORTED_UPLOAD_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {ext!r}; expected one of {_SUPPORTED_UPLOAD_EXT}.",
        )
    if _normalizer_factory is None:
        raise HTTPException(status_code=503, detail="Normalizer factory not initialized.")

    # STEP 1 — persist the upload to a temp file (the OCR client streams it on)
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        # STEP 2 — Layer 1: OCR via the OCR.space API
        try:
            rows = await OCRSpaceClient().extract_biomarkers_from_file(tmp_path)
        except OCRSpaceError as exc:
            logger.error("OCR.space error for patient=%s: %s", patient_id, exc)
            raise HTTPException(status_code=502, detail=f"OCR service error: {exc}") from exc

        # STEP 3 — adapt OCR rows → canonical biomarker dict
        biomarker_dict, ocr_metadata = Layer1ToLayer2Adapter().convert_ocr_rows_to_biomarker_dict(rows)
        if not biomarker_dict:
            raise HTTPException(
                status_code=422,
                detail="No biomarkers could be resolved from the report.",
            )
        logger.info(
            "POST /api/analyze-file: patient=%s, ocr_rows=%d, resolved=%d",
            patient_id, len(rows), len(biomarker_dict),
        )

        # STEP 4 — Layer 2→5 via the orchestrator (per-request normalizer)
        normalizer = _normalizer_factory(db)
        result: CBCAnalysisResult = await orchestrator.analyze_cbc(
            biomarker_values=biomarker_dict,
            patient_id=patient_id,
            metadata={"source_file": file.filename, **ocr_metadata},
            gender=gender,
            age_years=age_years,
            normalizer=normalizer,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error analysing file for patient=%s", patient_id)
        raise HTTPException(status_code=500, detail="Internal analysis error.") from exc
    finally:
        os.unlink(tmp_path)

    reports = await _build_reports(result, include_reports)
    serialized = result.to_dict()
    return AnalyzeCBCResponse(
        patient_id=result.patient_id,
        timestamp=result.timestamp,
        status=result.status,
        final_findings=serialized["final_findings"],
        clinical_flags=serialized["clinical_flags"],
        recommendations=serialized["recommendations"],
        reports=reports,
        audit_trail=serialized["audit_trail"],
        error_messages=result.error_messages,
        execution_time_ms=result.execution_time_ms,
    )


@router.get("/health", tags=["health"])
async def health() -> Dict[str, Any]:
    """Liveness + orchestrator-readiness probe."""
    return {
        "status": "ok",
        "orchestrator_ready": _orchestrator is not None,
        "db_ready": _sessionmaker is not None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
