"""
Integration tests for Layer 5 (Confidence Validation) orchestration.

Exercises the ConfidenceValidationEngine end-to-end with mock Layer 4 outputs:
validation rules, confidence calibration, severity→urgency mapping, impossible-
condition conflicts, clinical flags, failure handling, and the audit trail.
"""

import pytest

from models.graph_schemas import AuditTrail, EvidenceLink, Layer4Output, ValidatedFinding
from services.confidence_validation.confidence_calibrator import ConfidenceCalibrator
from services.confidence_validation.validation_engine import ConfidenceValidationEngine
from services.confidence_validation.validation_rules import load_validation_rules


# ── Mock builders ───────────────────────────────────────────────────────────
def _evidence(code: str, value: float, status: str, strength: float = 0.95) -> EvidenceLink:
    return EvidenceLink(
        biomarker_id=code, biomarker_name=code, evidence_strength=strength,
        narrative=f"{code}={value} ({status}) supports finding (strength: {strength})",
    )


def _finding(fid: str, name: str, confidence: float, evidence) -> ValidatedFinding:
    return ValidatedFinding(
        finding_id=fid, finding_name=name, confidence=confidence,
        severity=None, evidence_chain=list(evidence), source_pattern=fid,
    )


def _layer4(findings, features, biomarkers) -> Layer4Output:
    """Build a Layer4Output with the Layer-3 input embedded in the audit trail."""
    gen_features = [{"feature_id": f, "feature_type": "BINARY", "value": True} for f in features]
    norm = [{"biomarker_id": c, "value": v, "status": "LOW"} for c, v in biomarkers.items()]
    audit = AuditTrail(
        timestamp="2026-06-11T00:00:00+00:00",
        layer3_input={"generated_features": gen_features, "normalized_biomarkers": norm,
                      "detected_patterns": []},
        neo4j_queries_executed=[], nodes_queried=0, patterns_validated=len(findings),
        findings_extracted=len(findings), conflicts_detected=0,
        recommendations_generated=0, execution_time_ms=1.0,
    )
    return Layer4Output(validated_findings=list(findings), recommendations=[], conflicts=[],
                        audit_trail=audit, status="success", error_messages=[])


# Mock data ------------------------------------------------------------------
def MOCK_LAYER4_OUTPUT() -> Layer4Output:
    """A clean iron-deficiency anemia case (routine severity)."""
    return _layer4(
        findings=[_finding("iron_deficiency_anemia", "Iron Deficiency Anemia", 0.95,
                           [_evidence("HGB", 10.2, "LOW"), _evidence("MCV", 75.0, "LOW"),
                            _evidence("RDW", 16.5, "HIGH")])],
        features=["hemoglobin_low", "mcv_low", "rdw_high"],
        biomarkers={"HGB": 10.2, "MCV": 75.0, "RDW": 16.5},
    )


def MOCK_SEVERE_CASE() -> Layer4Output:
    """A critical anemia case (Hb 2.0 → critical)."""
    return _layer4(
        findings=[_finding("iron_deficiency_anemia", "Iron Deficiency Anemia", 0.95,
                           [_evidence("HGB", 2.0, "CRITICAL_LOW"), _evidence("MCV", 70.0, "LOW")])],
        features=["hemoglobin_low", "mcv_low"],
        biomarkers={"HGB": 2.0, "MCV": 70.0},
    )


def MOCK_CONFLICTING_CASE() -> Layer4Output:
    """Two impossible combinations present at once."""
    return _layer4(
        findings=[_finding("microcytic_anemia", "Microcytic Anemia", 0.8, [_evidence("HGB", 10.0, "LOW")]),
                  _finding("macrocytic_anemia", "Macrocytic Anemia", 0.8, [_evidence("HGB", 10.0, "LOW")])],
        features=["hemoglobin_low", "hemoglobin_high", "mcv_low", "mcv_high"],
        biomarkers={"HGB": 10.0},
    )


def MOCK_VALIDATION_FAILURE() -> Layer4Output:
    """IDA finding but MCV-low evidence is missing → validation must fail."""
    return _layer4(
        findings=[_finding("iron_deficiency_anemia", "Iron Deficiency Anemia", 0.9,
                           [_evidence("HGB", 10.2, "LOW")])],
        features=["hemoglobin_low"],          # missing required mcv_low
        biomarkers={"HGB": 10.2},
    )


@pytest.fixture
def engine() -> ConfidenceValidationEngine:
    rules = load_validation_rules()
    return ConfidenceValidationEngine(ConfidenceCalibrator(rules), rules)


# ── Tests ───────────────────────────────────────────────────────────────────
class TestConfidenceValidationEngine:
    async def test_end_to_end_validation(self, engine):
        """Full pipeline: Layer4 → validation → Layer5 with findings, flags, audit."""
        out = await engine.validate(MOCK_LAYER4_OUTPUT())
        assert out.final_findings and out.final_findings[0].finding_id == "iron_deficiency_anemia"
        assert isinstance(out.clinical_flags, dict)
        assert out.audit_trail.timestamp and out.audit_trail.execution_time_ms >= 0
        assert "iron_deficiency_anemia" in out.audit_trail.confidence_adjustments

    async def test_confidence_calibration(self, engine):
        """Confidence is adjusted; original and final are both tracked with factors."""
        out = await engine.validate(MOCK_LAYER4_OUTPUT())
        adj = out.audit_trail.confidence_adjustments["iron_deficiency_anemia"]
        assert adj["original"] == 0.95
        assert adj["final"] <= adj["original"]           # 3 evidence + routine → slight reduction
        assert set(adj["factors"]) == {"evidence_count", "consistency", "severity"}
        assert out.final_findings[0].final_confidence == adj["final"]

    async def test_severity_mapping(self, engine):
        """Value→severity classification and the resulting urgency."""
        cal = engine.calibrator
        assert cal.apply_severity_calibration("hemoglobin", 10.2) == "routine"
        assert cal.apply_severity_calibration("hemoglobin", 2.0) == "critical"
        out = await engine.validate(MOCK_SEVERE_CASE())
        assert out.final_findings[0].severity == "critical"
        assert out.clinical_flags["critical"]            # critical urgency flag raised

    async def test_impossible_conditions(self, engine):
        """Microcytic+macrocytic and high+low hemoglobin are both flagged as conflicts."""
        out = await engine.validate(MOCK_CONFLICTING_CASE())
        flagged = set(out.audit_trail.impossible_conditions_flagged)
        assert "impossible_microcytic_and_macrocytic" in flagged
        assert "impossible_high_and_low_hemoglobin" in flagged
        assert len(out.clinical_flags["CONFLICTS"]) >= 2

    async def test_clinical_flags(self, engine):
        """Critical findings get a flag with the right escalation; routine do not."""
        critical = await engine.validate(MOCK_SEVERE_CASE())
        assert critical.clinical_flags["critical"]
        assert critical.clinical_flags["critical"][0].escalation_level == "hematology_consult"
        routine = await engine.validate(MOCK_LAYER4_OUTPUT())
        assert not routine.clinical_flags["critical"] and not routine.clinical_flags["urgent"]

    async def test_validation_failure_handling(self, engine):
        """A finding missing a required feature fails validation and needs review."""
        out = await engine.validate(MOCK_VALIDATION_FAILURE())
        finding = out.final_findings[0]
        assert finding.validation_passed is False
        assert "review" in finding.clinical_notes.lower()
        assert finding.status == "REQUIRES_MANUAL_REVIEW"

    async def test_clinician_review_required(self, engine):
        """When validation fails, review is required and next steps are provided."""
        out = await engine.validate(MOCK_VALIDATION_FAILURE())
        assert out.clinician_review_required is True
        assert out.next_steps
        assert out.status in ("requires_manual_review", "partial")

    async def test_audit_trail(self, engine):
        """The audit trail records all checks, counts, and timing."""
        out = await engine.validate(MOCK_LAYER4_OUTPUT())
        audit = out.audit_trail
        assert audit.timestamp
        assert {"impossible_check", "evidence_check", "consistency_check", "severity_check"} \
            <= set(audit.validation_checks_performed)
        assert audit.execution_time_ms >= 0
        assert audit.rules_applied_count >= 1
        assert audit.findings_validated == 1
