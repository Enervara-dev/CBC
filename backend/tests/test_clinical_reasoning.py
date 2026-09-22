"""
Tests for Layer 4's rule-based clinical reasoning.

This is the layer that answers "so what": it turns "HGB 6.2, LOW" into "severe
anaemia; reduced oxygen-carrying capacity; check iron/B12/folate; at this level
consider transfusion". The suites below lock in that

  * every abnormal parameter is reported — coverage is not conditional on the
    value forming a recognised pattern,
  * multi-marker conditions fire with their evidence and follow-up,
  * the severity grading matches the clinical bands, and
  * nothing is reported for a normal panel.

All three panels are exercised, because the point of the reasoner is that CBC,
LFT and Lipid all get interpreted, not just the panel the graph happens to cover.
"""

import pytest

from domains.clinical_types import worst_severity
from models.feature_schemas import GeneratedFeature, Layer3Output
from models.normalization_schemas import NormalizedBiomarker
from services.clinical_reasoning import CompositeReasoner, RuleBasedClinicalReasoner


def _biomarker(code, value, status, unit="", ref_min=0.0, ref_max=0.0):
    return NormalizedBiomarker(
        biomarker_id=code, biomarker_name=code, value=value, unit=unit,
        reference_min=ref_min, reference_max=ref_max, gender="F", age_group="adult",
        lab_source="Any", status=status, deviation_from_min=0.0, deviation_percent=0.0,
        extraction_confidence=1.0,
    )


def _binary(feature_id, code=None):
    return GeneratedFeature(
        feature_id=feature_id, feature_name=feature_id, feature_type="BINARY",
        value=True, confidence=1.0, clinical_significance="",
        source_biomarkers=[code] if code else [],
    )


def _layer3(biomarkers, features=()):
    return Layer3Output(
        normalized_biomarkers=list(biomarkers),
        generated_features=list(features),
        detected_patterns=[],
    )


@pytest.fixture
def reasoner():
    return RuleBasedClinicalReasoner()


# ── Coverage: every abnormal parameter is explained ─────────────────────────
@pytest.mark.asyncio
class TestEveryAbnormalParameterIsReported:
    async def test_severely_low_haemoglobin_is_reported_and_escalated(self, reasoner):
        """The motivating case: a really low haemoglobin must say what it means."""
        out = await reasoner.reason(_layer3(
            [_biomarker("HGB", 6.2, "LOW", "g/dL", 12.0, 15.5)]
        ))
        finding = next(f for f in out.validated_findings if "Anaemia" in f.finding_name)
        assert finding.severity in ("urgent", "critical")
        assert "oxygen-carrying capacity" in finding.interpretation
        assert any("iron" in c.lower() for c in finding.consider)
        assert finding.critical_note                      # the "act now" sentence exists
        assert finding.evidence_chain[0].biomarker_id == "HGB"

    @pytest.mark.parametrize(
        "code,value,status,unit",
        [("HGB", 6.2, "LOW", "g/dL"), ("WBC", 1.2, "LOW", "K/uL"),
         ("PLT", 40.0, "LOW", "K/uL"), ("MCV", 72.0, "LOW", "fL"),
         ("RDW", 17.5, "HIGH", "%"), ("NEUT", 85.0, "HIGH", "%"),
         ("ALT", 180.0, "HIGH", "U/L"), ("TBIL", 4.0, "HIGH", "mg/dL"),
         ("ALB", 2.4, "LOW", "g/dL"), ("ALP", 300.0, "HIGH", "U/L"),
         ("CHOL", 268.0, "HIGH", "mg/dL"), ("LDL", 195.0, "HIGH", "mg/dL"),
         ("HDL", 28.0, "LOW", "mg/dL"), ("TRIG", 600.0, "HIGH", "mg/dL")],
    )
    async def test_each_panel_marker_produces_an_explained_finding(
        self, reasoner, code, value, status, unit
    ):
        """CBC, LFT and Lipid alike — no abnormal value passes without comment."""
        out = await reasoner.reason(_layer3([_biomarker(code, value, status, unit)]))
        assert out.validated_findings, f"{code} {status} produced no finding"
        finding = out.validated_findings[0]
        assert finding.interpretation, f"{code} {status} has no interpretation"
        assert finding.evidence_chain

    async def test_normal_values_produce_no_findings(self, reasoner):
        out = await reasoner.reason(_layer3([
            _biomarker("HGB", 14.0, "NORMAL", "g/dL"),
            _biomarker("ALT", 22.0, "NORMAL", "U/L"),
            _biomarker("LDL", 88.0, "NORMAL", "mg/dL"),
        ]))
        assert out.validated_findings == []
        assert out.diagnostics and "within their reference ranges" in out.diagnostics[0]

    async def test_findings_are_ordered_worst_first(self, reasoner):
        out = await reasoner.reason(_layer3([
            _biomarker("RDW", 15.0, "HIGH", "%"),          # routine
            _biomarker("HGB", 4.5, "LOW", "g/dL"),         # critical
        ]))
        assert out.validated_findings[0].severity == "critical"


# ── Severity grading ────────────────────────────────────────────────────────
@pytest.mark.asyncio
class TestSeverityGrading:
    @pytest.mark.parametrize(
        "value,expected",
        [(4.5, "critical"),
         (6.2, "critical"),      # below the restrictive transfusion threshold (7)
         (7.5, "urgent"),        # WHO severe anaemia (< 8)
         (10.5, "routine"),      # mild-moderate
         (14.0, "normal")],
    )
    async def test_haemoglobin_bands(self, reasoner, value, expected):
        out = await reasoner.reason(_layer3(
            [_biomarker("HGB", value, "LOW" if value < 12 else "NORMAL", "g/dL")]
        ))
        if expected == "normal":
            assert not out.validated_findings
        else:
            assert out.validated_findings[0].severity == expected

    @pytest.mark.parametrize(
        "value,expected",
        [(1500.0, "critical"),   # > 1000 U/L: ischaemic / toxic / acute viral
         (500.0, "urgent"),
         (80.0, "routine")],
    )
    async def test_alt_bands(self, reasoner, value, expected):
        out = await reasoner.reason(_layer3([_biomarker("ALT", value, "HIGH", "U/L")]))
        assert out.validated_findings[0].severity == expected

    async def test_triglycerides_pancreatitis_band_is_critical(self, reasoner):
        out = await reasoner.reason(_layer3([_biomarker("TRIG", 1500.0, "HIGH", "mg/dL")]))
        finding = out.validated_findings[0]
        assert finding.severity == "critical"
        assert "pancreatitis" in finding.critical_note.lower()


class TestSeverityHelper:
    def test_worst_severity_picks_the_worst_band(self):
        assert worst_severity(["routine", "critical", "normal"]) == "critical"
        assert worst_severity(["normal"]) == "normal"
        assert worst_severity([]) == "normal"


# ── Multi-marker conditions ─────────────────────────────────────────────────
@pytest.mark.asyncio
class TestConditions:
    async def test_iron_deficiency_pattern_fires_with_evidence(self, reasoner):
        out = await reasoner.reason(_layer3(
            [_biomarker("HGB", 9.0, "LOW", "g/dL"), _biomarker("MCV", 72.0, "LOW", "fL"),
             _biomarker("RDW", 17.0, "HIGH", "%")],
            [_binary("hemoglobin_low", "HGB"), _binary("mcv_low", "MCV"),
             _binary("rdw_high", "RDW")],
        ))
        ida = next(f for f in out.validated_findings if f.finding_id == "iron_deficiency_anemia")
        assert {e.biomarker_id for e in ida.evidence_chain} >= {"HGB", "MCV"}
        assert "iron" in ida.interpretation.lower()
        assert any("GI evaluation" in r.recommendation_name for r in out.recommendations)

    async def test_cholestatic_pattern_fires_for_lft(self, reasoner):
        out = await reasoner.reason(_layer3(
            [_biomarker("ALP", 300.0, "HIGH", "U/L"), _biomarker("GGT", 200.0, "HIGH", "U/L"),
             _biomarker("DBIL", 1.5, "HIGH", "mg/dL")],
            [_binary("alp_high", "ALP"), _binary("alp_xuln_high", "ALP"),   # 300 = 2.3 x ULN
             _binary("ggt_high", "GGT"), _binary("direct_bilirubin_high", "DBIL")],
        ))
        ids = {f.finding_id for f in out.validated_findings}
        assert "cholestasis" in ids
        assert any("ultrasound" in r.recommendation_name.lower() for r in out.recommendations)

    async def test_atherogenic_dyslipidaemia_fires_for_lipid(self, reasoner):
        out = await reasoner.reason(_layer3(
            [_biomarker("TRIG", 320.0, "HIGH", "mg/dL"), _biomarker("HDL", 28.0, "LOW", "mg/dL")],
            [_binary("triglycerides_high", "TRIG"), _binary("hdl_cholesterol_low", "HDL")],
        ))
        finding = next(f for f in out.validated_findings
                       if f.finding_id == "atherogenic_dyslipidemia")
        assert "insulin resistance" in finding.interpretation.lower()
        assert any("HbA1c" in r.recommendation_name for r in out.recommendations)

    async def test_contradictory_finding_blocks_a_condition(self, reasoner):
        """mcv_high contradicts iron deficiency; the condition must not fire."""
        out = await reasoner.reason(_layer3(
            [_biomarker("HGB", 9.0, "LOW", "g/dL"), _biomarker("MCV", 110.0, "HIGH", "fL")],
            [_binary("hemoglobin_low", "HGB"), _binary("mcv_low", "MCV"),
             _binary("mcv_high", "MCV")],
        ))
        assert "iron_deficiency_anemia" not in {f.finding_id for f in out.validated_findings}

    async def test_condition_supersedes_the_bare_biomarker_finding(self, reasoner):
        """A named condition and the per-marker finding must not both be shown."""
        out = await reasoner.reason(_layer3(
            [_biomarker("PLT", 40.0, "LOW", "K/uL")],
            [_binary("platelets_low", "PLT")],
        ))
        names = [f.finding_name.lower() for f in out.validated_findings]
        assert len(names) == len(set(names)), f"duplicate findings: {names}"


# ── Recommendations ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
class TestRecommendations:
    async def test_recommendations_are_deduped_and_urgency_sorted(self, reasoner):
        out = await reasoner.reason(_layer3(
            [_biomarker("HGB", 8.0, "LOW", "g/dL"), _biomarker("MCV", 70.0, "LOW", "fL")],
            [_binary("hemoglobin_low", "HGB"), _binary("mcv_low", "MCV")],
        ))
        ids = [r.recommendation_id for r in out.recommendations]
        assert len(ids) == len(set(ids)), "duplicate recommendations"
        urgencies = [r.urgency for r in out.recommendations]
        rank = {"stat": 0, "urgent": 1, "routine": 2}
        assert urgencies == sorted(urgencies, key=lambda u: rank.get(u, 3))

    async def test_critical_value_lifts_routine_follow_up_to_urgent(self, reasoner):
        out = await reasoner.reason(_layer3([_biomarker("TRIG", 1500.0, "HIGH", "mg/dL")]))
        assert any(r.urgency in ("urgent", "stat") for r in out.recommendations)


# ── Composite behaviour ─────────────────────────────────────────────────────
@pytest.mark.asyncio
class TestComposite:
    async def test_graph_failure_leaves_rule_findings_intact(self):
        class _BrokenGraph:
            async def reason(self, _):
                raise RuntimeError("Neo4j unreachable")

        composite = CompositeReasoner(graph_reasoner=_BrokenGraph())
        out = await composite.reason(_layer3([_biomarker("HGB", 6.2, "LOW", "g/dL")]))
        assert out.validated_findings, "a graph outage must not erase rule findings"
        assert any("Graph reasoning unavailable" in d for d in out.diagnostics)

    async def test_no_graph_configured_is_rule_only(self):
        composite = CompositeReasoner(graph_reasoner=None)
        out = await composite.reason(_layer3([_biomarker("ALT", 200.0, "HIGH", "U/L")]))
        assert out.validated_findings
        assert out.status == "success"

    async def test_graph_findings_are_merged_not_duplicated(self):
        base = await RuleBasedClinicalReasoner().reason(
            _layer3([_biomarker("HGB", 6.2, "LOW", "g/dL")])
        )

        class _Graph:
            async def reason(self, _):
                extra = base.model_copy(deep=True)
                new = extra.validated_findings[0].model_copy(deep=True)
                new.finding_id = "graph_only_finding"
                new.finding_name = "Graph-only finding"
                extra.validated_findings.append(new)
                return extra

        out = await CompositeReasoner(graph_reasoner=_Graph()).reason(
            _layer3([_biomarker("HGB", 6.2, "LOW", "g/dL")])
        )
        ids = [f.finding_id for f in out.validated_findings]
        assert "graph_only_finding" in ids
        assert len(ids) == len(set(ids)), "graph merge duplicated a finding"


# ── Threshold-defined conditions must clear their severity gate ─────────────
@pytest.mark.asyncio
class TestSeverityGatedConditions:
    """A binary feature says only "out of range". Conditions defined by a
    threshold — FH at LDL 190, pancreatitis risk at TG 500 — need more than
    that, or they fire on mildly abnormal values and the advice is wrong."""

    async def test_moderate_ldl_does_not_suggest_familial_hypercholesterolaemia(self, reasoner):
        out = await reasoner.reason(_layer3(
            [_biomarker("LDL", 178.0, "HIGH", "mg/dL")],
            [_binary("ldl_cholesterol_high", "LDL")],
        ))
        ids = {f.finding_id for f in out.validated_findings}
        assert "familial_hypercholesterolemia_suspected" not in ids
        assert not any("Cascade screening" in r.recommendation_name
                       for r in out.recommendations)

    async def test_ldl_above_190_does_suggest_it(self, reasoner):
        out = await reasoner.reason(_layer3(
            [_biomarker("LDL", 210.0, "HIGH", "mg/dL")],
            [_binary("ldl_cholesterol_high", "LDL")],
        ))
        ids = {f.finding_id for f in out.validated_findings}
        assert "familial_hypercholesterolemia_suspected" in ids
        assert any("Cascade screening" in r.recommendation_name for r in out.recommendations)

    async def test_moderate_triglycerides_are_not_a_pancreatitis_alert(self, reasoner):
        out = await reasoner.reason(_layer3(
            [_biomarker("TRIG", 420.0, "HIGH", "mg/dL")],
            [_binary("triglycerides_high", "TRIG")],
        ))
        assert "severe_hypertriglyceridemia" not in {f.finding_id for f in out.validated_findings}

    async def test_triglycerides_above_500_are(self, reasoner):
        out = await reasoner.reason(_layer3(
            [_biomarker("TRIG", 1200.0, "HIGH", "mg/dL")],
            [_binary("triglycerides_high", "TRIG")],
        ))
        assert "severe_hypertriglyceridemia" in {f.finding_id for f in out.validated_findings}


# ── A condition that restates one measured value stays an observation ───────
class TestMeasuredConditionMerge:
    """Found end-to-end on the live API: a real HDL of 39 (male, ref >= 40) came
    back as "Low HDL cholesterol" at 47% confidence, because the named condition
    replaced the measured finding and lost its observation status."""

    @pytest.mark.asyncio
    async def test_low_hdl_keeps_measured_confidence_and_rule_narrative(self, reasoner):
        out = await reasoner.reason(_layer3(
            [_biomarker("HDL", 39.0, "LOW", "mg/dL", 40.0, 0.0)],
            [_binary("hdl_cholesterol_low", "HDL")],
        ))
        hdl = [f for f in out.validated_findings if f.finding_name == "Low HDL cholesterol"]
        assert len(hdl) == 1
        finding = hdl[0]
        assert finding.source_pattern.startswith("biomarker:")   # Layer 5 treats it as measured
        assert "rule:low_hdl" in finding.source_pattern          # the condition is still recorded
        assert finding.confidence >= 0.95
        assert "protective threshold" in finding.interpretation   # the rule's narrative won

    def test_layer5_does_not_discount_the_merged_finding(self):
        from domains.registry import merged_validation_rules
        from services.confidence_validation.confidence_calibrator import ConfidenceCalibrator

        cal = ConfidenceCalibrator(merged_validation_rules())
        result = cal.calibrate_confidence(0.95, evidence_count=1, severity="routine", measured=True)
        assert result["final"] >= 0.85


# ── The same action worded twice is listed once ─────────────────────────────
class TestRecommendationHeadlineDedup:
    """Found on the live API: a real lipid report listed "Regular aerobic
    exercise" twice (once per interpretation table, different ids and wording)."""

    @staticmethod
    def _headlines(recs):
        return [" ".join(r.recommendation_name.split(" — ")[0].split(" (")[0].lower().split())
                for r in recs]

    @pytest.mark.asyncio
    async def test_low_hdl_lists_each_action_once(self, reasoner):
        out = await reasoner.reason(_layer3(
            [_biomarker("HDL", 39.0, "LOW", "mg/dL", 40.0, 0.0)],
            [_binary("hdl_cholesterol_low", "HDL")],
        ))
        heads = self._headlines(out.recommendations)
        assert heads.count("regular aerobic exercise") == 1
        assert len(heads) == len(set(heads))

    @pytest.mark.asyncio
    async def test_iron_studies_variants_collapse_to_one(self, reasoner):
        out = await reasoner.reason(_layer3(
            [_biomarker("HGB", 8.0, "LOW", "g/dL"), _biomarker("MCV", 70.0, "LOW", "fL"),
             _biomarker("MCH", 22.0, "LOW", "pg")],
            [_binary("hemoglobin_low", "HGB"), _binary("mcv_low", "MCV"), _binary("mch_low", "MCH")],
        ))
        assert self._headlines(out.recommendations).count("iron studies") == 1

    def test_most_urgent_duplicate_wins(self):
        from models.graph_schemas import Recommendation as Rec

        recs = [Rec(recommendation_id="a", recommendation_name="Iron studies", recommendation_type="TEST",
                    priority=3, urgency="routine", from_finding="x"),
                Rec(recommendation_id="b", recommendation_name="Iron studies (ferritin, TSAT)",
                    recommendation_type="TEST", priority=2, urgency="urgent", from_finding="y")]
        kept = RuleBasedClinicalReasoner._dedupe(recs)
        assert len(kept) == 1 and kept[0].urgency == "urgent"
