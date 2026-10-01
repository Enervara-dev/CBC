"""
Tests for POST /api/analyze-url — the adapter that lets the ENERVERA main
application hand over a presigned document URL and get back its own
LabProcessingService.extract() wire contract.

The download is faked at two seams:

  * ``api.routes.fetch_document_to_temp_file`` — swapped for a function that
    materialises real PDF-magic bytes on disk (the route unlinks the file in a
    ``finally``, so it must really exist);
  * ``api.routes.get_ocr_client`` — the same seam the analyze-file tests use,
    so OCR never touches the network.

The orchestrator is faked (it would need DB + Neo4j); the Layer-1 adapter and
the domain registry run for real — they are static, needing neither.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import api_key_middleware
from api.routes import (
    router, get_db,
    init_orchestrator, init_db, init_normalizer_factory, init_report_generator,
)
from orchestration.cbc_orchestrator import CBCAnalysisResult, NestedAuditTrail
from orchestration.document_fetcher import DocumentFetchError, UnsupportedDocumentError
from orchestration.ocr_space_client import OCRSpaceError

GOOD_KEY = "test-key-aaa"
SIGNED_URL = "https://bucket.s3.amazonaws.com/report.pdf?X-Amz-Signature=topsecret"

PDF_BYTES = b"%PDF-1.4 fake report"


def _biomarker(code, value, unit, ref_min, ref_max, confidence=0.95):
    """A duck-typed stand-in for services.normalization.normalizer.NormalizedBiomarker."""
    return SimpleNamespace(
        biomarker_id=code, value=value, unit=unit,
        reference_min=ref_min, reference_max=ref_max,
        extraction_confidence=confidence,
    )


def _audit():
    return NestedAuditTrail(
        timestamp="t", patient_id="p1", biomarker_input_count=5,
        layer2_normalization={}, layer3_features={}, layer4_graph_reasoning={},
        layer5_validation={"timestamp": "t"}, total_execution_ms=1.0, layer_timings={},
    )


def _result(status="success", errors=(), biomarkers=None):
    return CBCAnalysisResult(
        patient_id="p1", timestamp="t", status=status,
        final_findings=[], clinical_flags={}, audit_trail=_audit(),
        error_messages=list(errors), execution_time_ms=1.0,
        normalized_biomarkers=biomarkers or [],
    )


CBC_BIOMARKERS = [
    _biomarker("WBC", 5.2, "K/uL", 4.0, 11.0),
    _biomarker("HGB", 10.2, "g/dL", 12.0, 16.0),
    _biomarker("PLT", 250.0, "K/uL", 150.0, 410.0),
]

MIXED_BIOMARKERS = [
    _biomarker("HGB", 10.2, "g/dL", 12.0, 16.0),
    _biomarker("ALT", 180.0, "U/L", 7.0, 45.0),
    _biomarker("WBC", 5.2, "K/uL", 4.0, 11.0),
]


class FakeOCR:
    """Drives the real Layer-1 adapter from controlled OCR rows."""

    def __init__(self, rows):
        self.rows = rows

    async def extract_biomarkers_from_file(self, path):
        return self.rows


class FakeOrchestrator:
    """Captures analyze_cbc kwargs and returns a preset result."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    async def analyze_cbc(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


# Rows that resolve against the merged alias tables (Layer-1 adapter runs real).
GOOD_ROWS = [
    {"name": "WBC", "value": "5.2", "unit": "K/uL", "confidence": 0.9},
    {"name": "HGB", "value": "10.2", "unit": "g/dL", "confidence": 0.9},
    {"name": "PLT", "value": "250", "unit": "K/uL", "confidence": 0.9},
    {"name": "MCV", "value": "75", "unit": "fL", "confidence": 0.9},
    {"name": "RDW", "value": "16.5", "unit": "%", "confidence": 0.9},
]
# No alias in any registered panel — the adapter raises ValueError.
UNRESOLVABLE_ROWS = [
    {"name": "zzz-not-a-biomarker", "value": "1", "unit": "", "confidence": 0.9},
]


@pytest.fixture()
def downloads(monkeypatch, tmp_path):
    """
    Swap the downloader for one that writes real bytes to a per-test temp dir,
    so the route's finally-unlink has a real file and tests can assert no
    temp files leak.
    """
    created = []

    def fake_fetch(url, **kwargs):
        path = tmp_path / f"doc-{len(created)}.pdf"
        path.write_bytes(PDF_BYTES)
        created.append(path)
        return str(path), ".pdf"

    monkeypatch.setattr("api.routes.fetch_document_to_temp_file", fake_fetch)
    return created


def _client(monkeypatch, orchestrator, *, with_auth=False, ocr_rows=None, ocr_error=None):
    app = FastAPI()
    app.include_router(router)
    init_orchestrator(orchestrator)
    init_db(object())
    init_normalizer_factory(lambda db: None)
    init_report_generator(None)

    if ocr_error is not None:
        class _BrokenOCR:
            async def extract_biomarkers_from_file(self, path):
                raise ocr_error
        ocr = _BrokenOCR()
    else:
        ocr = FakeOCR(GOOD_ROWS if ocr_rows is None else ocr_rows)

    monkeypatch.setattr("api.routes.get_ocr_client", lambda: ocr)

    async def _fake_db():
        yield None
    app.dependency_overrides[get_db] = _fake_db

    if with_auth:
        app.middleware("http")(api_key_middleware)

    return TestClient(app)


class TestHappyPath:
    def test_extraction_contract_shape(self, downloads, monkeypatch):
        orch = FakeOrchestrator(_result(biomarkers=CBC_BIOMARKERS))
        client = _client(monkeypatch, orch, ocr_rows=GOOD_ROWS)
        r = client.post("/api/analyze-url", json={
            "document_url": SIGNED_URL, "report_id": "rpt-1", "user_id": "usr-1",
        })

        assert r.status_code == 200
        body = r.json()

        # The main app's wire contract.
        assert body["report_type"] == "CBC"
        assert body["report_date"] is None
        assert [p["name"] for p in body["panels"]] == ["Complete Blood Count"]
        results = {res["test_name"]: res for res in body["panels"][0]["results"]}
        assert set(results) == {"Hemoglobin", "Wbc", "Platelets"}
        hgb = results["Hemoglobin"]
        assert hgb["value"] == 10.2
        assert hgb["unit"] == "g/dL"
        assert hgb["reference_low"] == 12.0 and hgb["reference_high"] == 16.0
        assert hgb["reference_text"] == "12.0 - 16.0"
        assert hgb["confidence"] == 0.95
        assert hgb["value_text"] is None
        assert body["metadata"] == {
            "lab_name": None, "referring_doctor": None, "collected_at": None,
            "accession_number": None, "notes": None,
        }
        # The full CBC analysis rides along for debugging; the main app ignores it.
        assert body["analysis"]["status"] == "success"

    def test_caller_ids_and_context_propagate_to_orchestrator(self, downloads, monkeypatch):
        orch = FakeOrchestrator(_result(biomarkers=CBC_BIOMARKERS))
        client = _client(monkeypatch, orch, ocr_rows=GOOD_ROWS)
        r = client.post("/api/analyze-url", json={
            "document_url": SIGNED_URL, "report_id": "rpt-9", "user_id": "usr-9",
            "gender": "F", "age_years": 45, "pregnant": True, "file_name": "cbc.pdf",
        })

        assert r.status_code == 200
        call = orch.calls[0]
        assert call["patient_id"] == "rpt-9"  # report_id wins
        assert call["gender"] == "F"
        assert call["age_years"] == 45
        assert call["metadata"]["pregnant"] is True
        assert call["metadata"]["source_file"] == "cbc.pdf"

    def test_user_id_falls_back_when_no_report_id(self, downloads, monkeypatch):
        orch = FakeOrchestrator(_result(biomarkers=CBC_BIOMARKERS))
        client = _client(monkeypatch, orch, ocr_rows=GOOD_ROWS)
        client.post("/api/analyze-url", json={
            "document_url": SIGNED_URL, "user_id": "usr-only",
        })
        assert orch.calls[0]["patient_id"] == "usr-only"

    def test_mixed_report_primary_panel_first(self, downloads, monkeypatch):
        orch = FakeOrchestrator(_result(biomarkers=MIXED_BIOMARKERS))
        client = _client(monkeypatch, orch, ocr_rows=GOOD_ROWS)
        r = client.post("/api/analyze-url", json={"document_url": SIGNED_URL})

        body = r.json()
        # 2 CBC codes vs 1 LFT — CBC is unambiguously the primary panel.
        assert body["report_type"] == "CBC"
        assert [p["name"] for p in body["panels"]] == [
            "Complete Blood Count", "Liver Function Test",
        ]
        lft = body["panels"][1]
        assert lft["results"][0]["test_name"] == "Alt"  # service display convention
        assert lft["results"][0]["unit"] == "U/L"

    def test_temp_files_cleaned_up(self, downloads, monkeypatch):
        orch = FakeOrchestrator(_result(biomarkers=CBC_BIOMARKERS))
        client = _client(monkeypatch, orch, ocr_rows=GOOD_ROWS)
        r = client.post("/api/analyze-url", json={"document_url": SIGNED_URL})
        assert r.status_code == 200
        assert [p for p in downloads if p.exists()] == []


class TestGracefulDegradation:
    def test_no_biomarkers_resolved_returns_empty_panels(self, downloads, monkeypatch):
        """The main app turns empty panels into its friendly NO_VALUES_FOUND."""
        orch = FakeOrchestrator(_result())  # never reached
        client = _client(monkeypatch, orch, ocr_rows=UNRESOLVABLE_ROWS)
        r = client.post("/api/analyze-url", json={"document_url": SIGNED_URL})

        assert r.status_code == 200
        body = r.json()
        assert body["panels"] == []
        assert body["report_type"] == "OTHER"
        assert any("no_biomarkers_resolved" in m for m in body["error_messages"])
        assert body["analysis"] is None
        assert orch.calls == []  # the pipeline stopped at the adapter

    def test_pipeline_error_without_layer2_prefix_returns_200(self, downloads, monkeypatch):
        """Non-fatal pipeline degradation is surfaced in-band, not as an HTTP error."""
        orch = FakeOrchestrator(_result(
            status="partial", errors=["layer4: Neo4j unavailable"],
            biomarkers=CBC_BIOMARKERS,
        ))
        client = _client(monkeypatch, orch, ocr_rows=GOOD_ROWS)
        r = client.post("/api/analyze-url", json={"document_url": SIGNED_URL})

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "partial"
        assert body["panels"]  # values still delivered

    def test_layer2_fatal_is_a_502(self, downloads, monkeypatch):
        """A Layer-2 failure (DB down, ranges missing) is OUR fault, not the document's."""
        orch = FakeOrchestrator(_result(
            status="error", errors=["layer2: no normalizer available"],
        ))
        client = _client(monkeypatch, orch, ocr_rows=GOOD_ROWS)
        r = client.post("/api/analyze-url", json={"document_url": SIGNED_URL})
        assert r.status_code == 502

    def test_ocr_failure_is_a_502(self, downloads, monkeypatch):
        orch = FakeOrchestrator(_result())
        client = _client(monkeypatch, orch, ocr_error=OCRSpaceError("OCR.space is down"))
        r = client.post("/api/analyze-url", json={"document_url": SIGNED_URL})
        assert r.status_code == 502
        assert "OCR" in r.json()["detail"]


class TestDownloadFailures:
    def test_bad_scheme_is_422(self, downloads, monkeypatch):
        monkeypatch.setattr(
            "api.routes.fetch_document_to_temp_file",
            lambda url, **kw: (_ for _ in ()).throw(
                UnsupportedDocumentError("URL scheme must be http(s), got 'ftp'")),
        )
        orch = FakeOrchestrator(_result(biomarkers=CBC_BIOMARKERS))
        client = _client(monkeypatch, orch)
        r = client.post("/api/analyze-url", json={"document_url": "ftp://x/report.pdf"})
        assert r.status_code == 422

    def test_download_failure_is_502(self, downloads, monkeypatch):
        monkeypatch.setattr(
            "api.routes.fetch_document_to_temp_file",
            lambda url, **kw: (_ for _ in ()).throw(
                DocumentFetchError("GET https://bucket/report.pdf returned status 403")),
        )
        orch = FakeOrchestrator(_result(biomarkers=CBC_BIOMARKERS))
        client = _client(monkeypatch, orch)
        r = client.post("/api/analyze-url", json={"document_url": SIGNED_URL})
        assert r.status_code == 502


class TestAuth:
    def test_requires_api_key_when_keys_configured(self, downloads, monkeypatch):
        monkeypatch.setenv("CBC_API_KEYS", GOOD_KEY)
        orch = FakeOrchestrator(_result(biomarkers=CBC_BIOMARKERS))
        client = _client(monkeypatch, orch, with_auth=True, ocr_rows=GOOD_ROWS)

        missing = client.post("/api/analyze-url", json={"document_url": SIGNED_URL})
        wrong = client.post(
            "/api/analyze-url", json={"document_url": SIGNED_URL},
            headers={"X-API-Key": "wrong"},
        )
        ok = client.post(
            "/api/analyze-url", json={"document_url": SIGNED_URL},
            headers={"X-API-Key": GOOD_KEY},
        )
        assert missing.status_code == 401
        assert wrong.status_code == 401
        assert ok.status_code == 200

    def test_fails_closed_when_no_keys_configured(self, downloads, monkeypatch):
        monkeypatch.delenv("CBC_API_KEYS", raising=False)
        orch = FakeOrchestrator(_result(biomarkers=CBC_BIOMARKERS))
        client = _client(monkeypatch, orch, with_auth=True, ocr_rows=GOOD_ROWS)
        r = client.post(
            "/api/analyze-url", json={"document_url": SIGNED_URL},
            headers={"X-API-Key": GOOD_KEY},
        )
        assert r.status_code == 503
