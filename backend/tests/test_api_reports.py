"""
API wiring tests for the two fixed gaps:
  1. Layer 6 reports are reachable over HTTP (opt-in ``include_reports``).
  2. Layer 4 ``recommendations`` are surfaced in the analyze response.

Uses a minimal app (no lifespan), a fake orchestrator, and the real
``ReportGenerator`` with a fake LLM — no DB, Neo4j, or network.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import (
    router, get_db,
    init_orchestrator, init_db, init_normalizer_factory, init_report_generator,
)
from orchestration.cbc_orchestrator import CBCAnalysisResult, NestedAuditTrail
from services.presentation.report_generator import ReportGenerator
from services.presentation.llm_client import LLMError
from models.validation_schemas import FinalFinding, ClinicalFlag
from models.graph_schemas import Recommendation


class FakeLLM:
    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        audience = "patient" if "patient communication" in system else "clinician"
        n = 400 if audience == "patient" else 1000
        return f"{audience.upper()} REPORT. " + " ".join(["word"] * n)


class FakeOrchestrator:
    def __init__(self, result):
        self.result = result

    async def analyze_cbc(self, **kwargs):
        return self.result


def _result(with_findings=True):
    findings = []
    if with_findings:
        findings = [FinalFinding(
            finding_id="iron_deficiency_anemia", finding_name="Iron Deficiency Anemia",
            final_confidence=0.86, severity="routine", validation_passed=True,
            clinical_notes="Microcytic, hypochromic.",
            source_evidence=["HGB=10.2 (LOW)"], status="APPROVED_FOR_REVIEW",
        )]
    recs = [Recommendation(
        recommendation_id="order_iron_studies", recommendation_name="Order iron studies",
        recommendation_type="TEST", priority=2, urgency="routine",
        from_finding="iron_deficiency_anemia",
    )]
    audit = NestedAuditTrail(
        timestamp="t", patient_id="p1", biomarker_input_count=5,
        layer2_normalization={}, layer3_features={}, layer4_graph_reasoning={},
        layer5_validation={"timestamp": "t"}, total_execution_ms=1.0, layer_timings={},
    )
    return CBCAnalysisResult(
        patient_id="p1", timestamp="t", status="success",
        final_findings=findings, clinical_flags={"routine": []},
        recommendations=recs, audit_trail=audit, error_messages=[], execution_time_ms=1.0,
    )


def _client(result, report_generator):
    app = FastAPI()
    app.include_router(router)
    init_orchestrator(FakeOrchestrator(result))
    init_db(object())                       # non-None so get_db's guard passes (overridden anyway)
    init_normalizer_factory(lambda db: None)
    init_report_generator(report_generator)

    async def _fake_db():
        yield None
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app)


_BODY = {"patient_id": "p1", "biomarker_values": {"HGB": 10.2, "MCV": 75, "RDW": 16.5, "WBC": 5.2, "PLT": 250}}


class TestRecommendations:
    def test_recommendations_surfaced(self):
        client = _client(_result(), None)
        r = client.post("/api/analyze", json=_BODY)
        assert r.status_code == 200
        body = r.json()
        assert body["recommendations"] and body["recommendations"][0]["recommendation_id"] == "order_iron_studies"


class TestReportWiring:
    def test_no_reports_by_default(self):
        client = _client(_result(), ReportGenerator(FakeLLM()))
        body = client.post("/api/analyze", json=_BODY).json()
        assert body["reports"] is None

    def test_reports_generated_on_request(self):
        client = _client(_result(), ReportGenerator(FakeLLM()))
        body = client.post("/api/analyze", json={**_BODY, "include_reports": True}).json()
        assert body["reports"] is not None
        assert body["reports"]["status"] == "success"
        assert body["reports"]["patient_report"]["generated"] is True
        assert body["reports"]["exports"]["hl7_v2"].startswith("MSH")

    def test_reports_unavailable_without_generator(self):
        client = _client(_result(), None)
        body = client.post("/api/analyze", json={**_BODY, "include_reports": True}).json()
        assert body["reports"]["status"] == "unavailable"

    def test_reports_skipped_when_no_findings(self):
        client = _client(_result(with_findings=False), ReportGenerator(FakeLLM()))
        body = client.post("/api/analyze", json={**_BODY, "include_reports": True}).json()
        assert body["reports"]["status"] == "skipped"
