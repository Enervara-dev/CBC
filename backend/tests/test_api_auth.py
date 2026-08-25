"""
X-API-Key enforcement.

CBC sits behind an internal ALB today because it has no auth. Before it can be
moved to the public ALB, `/api/analyze` and `/api/analyze-file` must be closed:
every call to them spends OCR.space credits and Gemini tokens.

The single most important case here is `test_health_is_public_with_no_key_set`.
The ALB health check cannot send custom headers, so if `/api/health` ever
required a key, every target would go permanently unhealthy and the service
would be down. That test is the guard against a very expensive mistake.

The app is built the same way the other API tests build it — a minimal FastAPI
instance with the real router and no lifespan — so no DB, Neo4j or network is
touched.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import api_key_middleware
from api.routes import router, get_db, get_orchestrator

GOOD_KEY = "test-key-aaa"
OTHER_KEY = "test-key-bbb"


class _StubOrchestrator:
    """Never reached in these tests: auth rejects before the route body runs."""

    async def analyze(self, *args, **kwargs):  # pragma: no cover - see docstring
        raise AssertionError("route body ran despite failing auth")


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    @app.get("/", tags=["health"])
    async def root() -> dict[str, str]:
        return {"service": "ENERVERA CBC Analysis API"}

    app.middleware("http")(api_key_middleware)
    app.dependency_overrides[get_orchestrator] = lambda: _StubOrchestrator()

    async def _fake_db():
        yield None

    app.dependency_overrides[get_db] = _fake_db
    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_app())


PAYLOAD = {"patient_id": "p1", "biomarkers": {"hemoglobin": 13.5}}


# ---------------------------------------------------------------------------
# Public paths
# ---------------------------------------------------------------------------

def test_health_is_public_with_no_key_set(client, monkeypatch):
    """
    THE critical case. The ALB health check sends no headers and there may be
    no key configured yet; health must still answer 200 or every target in the
    target group is marked unhealthy.
    """
    monkeypatch.delenv("CBC_API_KEYS", raising=False)
    r = client.get("/api/health")
    assert r.status_code == 200


def test_health_is_public_even_when_keys_are_configured(client, monkeypatch):
    monkeypatch.setenv("CBC_API_KEYS", GOOD_KEY)
    assert client.get("/api/health").status_code == 200


def test_banner_and_docs_stay_public(client, monkeypatch):
    monkeypatch.setenv("CBC_API_KEYS", GOOD_KEY)
    for path in ("/", "/openapi.json"):
        assert client.get(path).status_code == 200, path


# ---------------------------------------------------------------------------
# Protected paths
# ---------------------------------------------------------------------------

def test_analyze_rejects_missing_header(client, monkeypatch):
    monkeypatch.setenv("CBC_API_KEYS", GOOD_KEY)
    r = client.post("/api/analyze", json=PAYLOAD)
    assert r.status_code == 401
    assert "X-API-Key" in r.json()["detail"]


def test_analyze_rejects_wrong_header(client, monkeypatch):
    monkeypatch.setenv("CBC_API_KEYS", GOOD_KEY)
    r = client.post("/api/analyze", json=PAYLOAD, headers={"X-API-Key": "nope"})
    assert r.status_code == 401


def test_analyze_passes_auth_with_correct_header(client, monkeypatch):
    """
    Auth lets the request through. The route body then fails on its own (no
    real orchestrator), which is fine: the assertion is that we got PAST the
    middleware, i.e. the response is not a 401 or 503.
    """
    monkeypatch.setenv("CBC_API_KEYS", GOOD_KEY)
    r = client.post("/api/analyze", json=PAYLOAD, headers={"X-API-Key": GOOD_KEY})
    assert r.status_code not in (401, 503)


def test_any_configured_key_is_accepted(client, monkeypatch):
    """Comma-separated list exists so keys can rotate and differ per caller."""
    monkeypatch.setenv("CBC_API_KEYS", f"{GOOD_KEY}, {OTHER_KEY}")
    for key in (GOOD_KEY, OTHER_KEY):
        r = client.post("/api/analyze", json=PAYLOAD, headers={"X-API-Key": key})
        assert r.status_code not in (401, 503), key


def test_analyze_file_is_protected_too(client, monkeypatch):
    """The upload route is the expensive one: OCR credits plus Gemini tokens."""
    monkeypatch.setenv("CBC_API_KEYS", GOOD_KEY)
    r = client.post(
        "/api/analyze-file",
        files={"file": ("cbc.pdf", b"%PDF-1.4", "application/pdf")},
        data={"patient_id": "p1"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------

def test_unset_keys_returns_503_not_open_access(client, monkeypatch):
    """
    A missing secret must degrade to "unavailable", never to "unauthenticated
    access permitted".
    """
    monkeypatch.delenv("CBC_API_KEYS", raising=False)
    r = client.post("/api/analyze", json=PAYLOAD, headers={"X-API-Key": GOOD_KEY})
    assert r.status_code == 503


def test_empty_and_whitespace_only_keys_also_fail_closed(client, monkeypatch):
    for value in ("", "   ", ",", " , ,, "):
        monkeypatch.setenv("CBC_API_KEYS", value)
        r = client.post("/api/analyze", json=PAYLOAD, headers={"X-API-Key": GOOD_KEY})
        assert r.status_code == 503, repr(value)


# ---------------------------------------------------------------------------
# Hygiene
# ---------------------------------------------------------------------------

def test_rejection_is_logged_without_the_presented_key(client, monkeypatch, caplog):
    """Rejected keys are still secrets and these logs land in CloudWatch."""
    monkeypatch.setenv("CBC_API_KEYS", GOOD_KEY)
    secret = "super-secret-attempt"
    with caplog.at_level("WARNING"):
        client.post("/api/analyze", json=PAYLOAD, headers={"X-API-Key": secret})
    assert "/api/analyze" in caplog.text
    assert secret not in caplog.text


def test_docs_advertise_the_scheme_so_swagger_shows_authorize(client):
    schemes = client.get("/openapi.json").json()["components"]["securitySchemes"]
    assert any(s.get("name") == "X-API-Key" for s in schemes.values())
