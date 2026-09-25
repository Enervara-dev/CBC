"""
Clinical accuracy — end-to-end regression cases, reviewed as a clinician would.

Each case runs the real Layers 2→5 (normalisation against the seeded reference
ranges, features, rule reasoning, confidence validation) on an in-memory
database and asserts what a doctor reading the report would insist on: the
right condition named, the wrong ones *not* named, and the right urgency.

Every assertion guards an error found in a structured review of the pipeline's
output across CBC, LFT and Lipid — the docstrings say what the pipeline used to
report.
"""

import asyncio
from typing import Any, Dict

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from db.models import Base, ReferenceRange
from domains.registry import all_domains, merged_feature_registry, merged_validation_rules
from orchestration.cbc_orchestrator import CBCOrchestrator
from services.clinical_reasoning import CompositeReasoner
from services.confidence_validation.confidence_calibrator import ConfidenceCalibrator
from services.confidence_validation.validation_engine import ConfidenceValidationEngine
from services.feature_generation.feature_generator import FeatureGenerator
from services.normalization.normalizer import DataNormalizer
from services.normalization.reference_lookup import ReferenceRangeLookup
from services.normalization.unit_converter import UnitConverter


async def _analyse(values, gender, age, metadata) -> Dict[str, Any]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncSession(engine) as session:
            for domain in all_domains():
                session.add_all(ReferenceRange(**row) for row in domain.reference_range_rows())
            await session.commit()
            rules = merged_validation_rules()
            orchestrator = CBCOrchestrator(
                DataNormalizer(session, UnitConverter, ReferenceRangeLookup(session)),
                FeatureGenerator(merged_feature_registry()),
                CompositeReasoner(),
                ConfidenceValidationEngine(ConfidenceCalibrator(rules), rules),
            )
            result = await orchestrator.analyze_cbc(
                {code: float(v) for code, v in values.items()}, "case",
                metadata=dict(metadata), gender=gender, age_years=age,
            )
            return result.to_dict()
    finally:
        await engine.dispose()


class Report:
    """The parts of a result a reader looks at, keyed for assertions."""

    def __init__(self, raw: Dict[str, Any]):
        self.raw = raw
        self.findings = {f["finding_id"]: f for f in raw["final_findings"]}
        self.by_name = {f["finding_name"]: f for f in raw["final_findings"]}
        self.recommendations = raw["recommendations"]

    def urgent_recommendations(self):
        return [r for r in self.recommendations if r["urgency"] in ("urgent", "stat")]


def analyse(values, gender="M", age=40, **metadata) -> Report:
    return Report(asyncio.run(_analyse(values, gender, age, metadata)))


# ── CBC ─────────────────────────────────────────────────────────────────────
class TestAnaemia:
    NORMOCYTIC = {"HGB": 12.5, "MCV": 88, "RDW": 13.0, "WBC": 7.0, "PLT": 250}

    def test_out_of_range_is_never_graded_normal(self):
        """Hb 12.5 is below a man's 13.5 limit; it was reported "Anaemia — normal"."""
        assert analyse(self.NORMOCYTIC, "M").by_name["Anaemia"]["severity"] == "routine"
        assert analyse(self.NORMOCYTIC, "F").findings == {}      # normal for a woman

    @pytest.mark.parametrize("hgb,expected", [(6.2, "critical"), (7.5, "urgent"),
                                              (10.5, "routine")])
    def test_grades_follow_who_severity_and_the_transfusion_threshold(self, hgb, expected):
        """Hb 5.8 was graded urgent; WHO severe is < 8 and transfusion is considered < 7."""
        r = analyse({"HGB": hgb, "MCV": 72, "RDW": 18.0, "WBC": 6.0, "PLT": 300}, "F", 38)
        assert r.by_name["Anaemia"]["severity"] == expected

    def test_thalassaemia_trait_is_not_called_iron_deficiency(self):
        """Mentzer 10.5 with a normal RDW was labelled urgent iron deficiency anaemia."""
        r = analyse({"HGB": 12.2, "RBC": 6.1, "MCV": 64, "MCH": 20, "RDW": 13.2,
                     "WBC": 7.5, "PLT": 250}, "M", 26)
        assert "thalassemia_trait" in r.findings
        assert not {"iron_deficiency_anemia", "microcytic_anemia"} & r.findings.keys()
        assert not r.raw["clinical_flags"].get("urgent")

    def test_iron_deficiency_replaces_generic_and_speculative_readings(self):
        """Iron deficiency, microcytic anaemia and chronic disease all fired together."""
        r = analyse({"HGB": 10.8, "RBC": 4.1, "MCV": 74, "MCH": 23, "RDW": 16.8,
                     "WBC": 7.0, "PLT": 420}, "F", 32)
        assert "iron_deficiency_anemia" in r.findings
        assert not ({"microcytic_anemia", "chronic_disease_anemia", "thalassemia_trait"}
                    & r.findings.keys())

    def test_megaloblastic_range_needs_an_mcv_above_110(self):
        """An alcohol-pattern MCV of 104 was labelled B12 *and* folate deficiency."""
        r = analyse({"HGB": 12.6, "MCV": 104, "RDW": 14.9, "WBC": 6.2, "PLT": 118}, "M", 49)
        assert "macrocytic_anemia" in r.findings
        assert not {"vitamin_b12_deficiency", "folate_deficiency"} & r.findings.keys()

    def test_a_critical_value_does_not_make_every_suggestion_urgent(self):
        r = analyse({"HGB": 5.8, "MCV": 66, "MCH": 19, "RDW": 19.5, "WBC": 6.1,
                     "PLT": 480}, "F", 41)
        urgency = {rec["recommendation_id"]: rec["urgency"] for rec in r.recommendations}
        assert urgency["iron_studies"] == "urgent"
        assert urgency["coeliac_screen"] == "routine"


class TestWhiteCells:
    def test_neutropenia_is_judged_on_the_absolute_count(self):
        """38% neutrophils at a WBC of 12 is an ANC of 4.6 — it was called neutropenia."""
        r = analyse({"HGB": 14.0, "MCV": 89, "RDW": 12.9, "WBC": 12.0, "NEUT": 38,
                     "LYMPH": 52, "PLT": 240}, "M", 19)
        assert "Neutropenia" not in r.by_name
        assert "Low neutrophil percentage" not in r.by_name
        assert "Lymphocytosis" in r.by_name                          # ALC 6.24
        derived = {d["code"] for d in r.raw["audit_trail"]["derived_biomarkers"]}
        assert derived == {"ANC", "ALC"}

    def test_severe_neutropenia_is_critical(self):
        """WBC 0.8 with 20% neutrophils (ANC 0.16) was graded only urgent."""
        r = analyse({"HGB": 11.0, "MCV": 92, "RDW": 14.0, "WBC": 0.8, "NEUT": 20,
                     "LYMPH": 70, "PLT": 95}, "M", 55)
        assert r.by_name["Neutropenia"]["severity"] == "critical"
        assert r.findings["pancytopenia"]["severity"] == "critical"

    def test_reported_absolute_count_is_used_as_is(self):
        r = analyse({"HGB": 13.0, "MCV": 88, "RDW": 13.0, "WBC": 3.0, "NEUT": 30,
                     "ANC": 0.9, "PLT": 210}, "F", 45)
        assert r.by_name["Neutropenia"]["severity"] == "urgent"
        assert "ANC" not in {d["code"] for d in r.raw["audit_trail"]["derived_biomarkers"]}

    def test_pancytopenia_is_urgent_even_when_each_line_is_mild(self):
        """Hb 8.9, WBC 3.6, PLT 130 was three routine findings and "immune compromise"."""
        r = analyse({"HGB": 8.9, "MCV": 112, "MCH": 36, "RDW": 16.0, "WBC": 3.6,
                     "NEUT": 55, "LYMPH": 38, "PLT": 130}, "M", 67)
        assert r.findings["pancytopenia"]["severity"] == "urgent"
        assert "vitamin_b12_deficiency" in r.findings
        assert not {"macrocytic_anemia", "folate_deficiency"} & r.findings.keys()

    def test_leukaemia_like_count_is_not_called_infection(self):
        """WBC 85, lymphocyte-predominant, with anaemia and low platelets was "acute infection"."""
        r = analyse({"HGB": 8.0, "MCV": 95, "RDW": 16.0, "WBC": 85.0, "NEUT": 25,
                     "LYMPH": 70, "PLT": 60}, "M", 63)
        assert r.findings["marked_leukocytosis"]["severity"] == "critical"
        assert "acute_infection" not in r.findings

    def test_polycythaemia_is_named_and_not_called_infection(self):
        r = analyse({"HGB": 19.2, "HCT": 58, "RBC": 6.6, "MCV": 88, "RDW": 13.5,
                     "WBC": 12.5, "PLT": 520}, "M", 58)
        assert r.findings["polycythemia"]["severity"] == "urgent"
        assert "acute_infection" not in r.findings


class TestPlatelets:
    @pytest.mark.parametrize("plt,expected,significant",
                             [(135, "routine", False), (40, "urgent", True),
                              (12, "critical", True)])
    def test_thrombocytopenia_grading_and_follow_up(self, plt, expected, significant):
        """A platelet count of 135 used to trigger urgent haematology referral."""
        r = analyse({"HGB": 13.5, "MCV": 90, "RDW": 13.0, "WBC": 6.5, "PLT": plt}, "F", 24)
        assert r.by_name["Thrombocytopenia"]["severity"] == expected
        assert ("severe_thrombocytopenia" in r.findings) is significant
        assert bool(r.urgent_recommendations()) is significant


# ── LFT ─────────────────────────────────────────────────────────────────────
class TestLiverPatterns:
    def test_fatty_liver_pattern_is_not_called_alcohol_or_viral(self):
        r = analyse({"ALT": 68, "AST": 45, "ALP": 95, "GGT": 70, "TBIL": 0.8, "ALB": 4.3},
                    "M", 44)
        assert "nafld_suspected" in r.findings
        assert not {"alcoholic_liver_disease", "viral_hepatitis"} & r.findings.keys()

    def test_alcohol_pattern_requires_ast_more_than_twice_alt(self):
        r = analyse({"ALT": 70, "AST": 180, "ALP": 140, "GGT": 420, "TBIL": 1.6, "ALB": 3.3},
                    "M", 52)
        assert "alcoholic_liver_disease" in r.findings
        assert not ({"nafld_suspected", "cholestasis", "viral_hepatitis"}
                    & r.findings.keys())

    def test_acute_hepatitis_with_jaundice(self):
        """ALT 1650 was also labelled alcohol-related and biliary obstruction."""
        r = analyse({"ALT": 1650, "AST": 1200, "ALP": 180, "GGT": 150, "TBIL": 6.8,
                     "DBIL": 4.9, "IBIL": 1.9, "ALB": 3.9}, "M", 31)
        assert r.findings["viral_hepatitis"]["severity"] == "critical"
        assert "acute_liver_failure_risk" in r.findings
        assert not ({"biliary_obstruction", "alcoholic_liver_disease", "cholestasis"}
                    & r.findings.keys())
        assert any(rec["recommendation_id"] == "paracetamol_level" and rec["urgency"] == "stat"
                   for rec in r.recommendations)

    def test_acute_hepatitis_without_jaundice_is_not_liver_failure(self):
        r = analyse({"ALT": 450, "AST": 380, "ALP": 110, "TBIL": 1.0, "ALB": 4.2}, "M", 35)
        assert r.findings["viral_hepatitis"]["severity"] == "urgent"
        assert "acute_liver_failure_risk" not in r.findings

    def test_obstructive_jaundice_is_not_liver_failure(self):
        """ALP 620 with conjugated bilirubin 7.8 was reported as liver-failure risk."""
        r = analyse({"ALT": 110, "AST": 90, "ALP": 620, "GGT": 580, "TBIL": 9.5,
                     "DBIL": 7.8, "IBIL": 1.7, "ALB": 3.8}, "F", 61)
        assert r.findings["biliary_obstruction"]["severity"] == "urgent"
        assert not ({"acute_liver_failure_risk", "hepatocellular_injury", "viral_hepatitis"}
                    & r.findings.keys())

    def test_raised_alp_with_normal_ggt_points_to_bone(self):
        """This used to recommend an urgent biliary ultrasound."""
        r = analyse({"ALT": 24, "AST": 26, "ALP": 310, "GGT": 25, "TBIL": 0.6, "ALB": 4.2},
                    "F", 68)
        assert "isolated_alp_elevation" in r.findings
        assert "cholestasis" not in r.findings
        assert not r.urgent_recommendations()

    def test_gilbert_needs_normal_enzymes_and_no_anaemia(self):
        gilbert = analyse({"ALT": 25, "AST": 22, "ALP": 80, "GGT": 20, "TBIL": 2.1,
                           "DBIL": 0.3, "IBIL": 1.8, "ALB": 4.5}, "M", 23)
        assert gilbert.findings["gilbert_syndrome"]["final_confidence"] >= 0.8

        haemolysis = analyse({"HGB": 9.1, "MCV": 98, "RDW": 16.5, "WBC": 7.0, "PLT": 260,
                              "ALT": 22, "AST": 35, "ALP": 90, "GGT": 25, "TBIL": 3.0,
                              "DBIL": 0.4, "IBIL": 2.6, "ALB": 4.2}, "F", 27)
        # Direct 0.4 of total 3.0 is 13% conjugated: unconjugated, so with anaemia
        # this is haemolysis — not Gilbert, and not "conjugated hyperbilirubinaemia".
        assert "gilbert_syndrome" not in haemolysis.findings
        assert "haemolysis_suspected" in haemolysis.findings
        assert "biliary_obstruction" not in haemolysis.findings

    def test_suspected_cirrhosis_is_flagged_not_called_obstruction(self):
        """This used to carry a STAT "assess for cholangitis" with no flag at all."""
        r = analyse({"ALT": 48, "AST": 72, "ALP": 130, "TBIL": 2.2, "DBIL": 0.9, "TP": 7.0,
                     "ALB": 2.6, "GLOB": 4.4, "AGR": 0.59}, "M", 57)
        assert r.findings["cirrhosis_suspected"]["severity"] == "urgent"
        assert "biliary_obstruction" not in r.findings
        assert not any(rec["urgency"] == "stat" for rec in r.recommendations)

    def test_pregnancy_reference_ranges_apply(self):
        """A third-trimester ALP of 210 was flagged as cholestasis."""
        values = {"ALT": 18, "AST": 20, "ALP": 210, "TBIL": 0.5, "ALB": 3.1}
        assert analyse(values, "F", 30, pregnant=True).findings == {}
        assert "alp_high_finding" in analyse(values, "F", 30).findings


# ── Lipid ───────────────────────────────────────────────────────────────────
class TestLipids:
    def test_severe_hypertriglyceridaemia_is_critical_and_certain(self):
        """A triglyceride of 1450 was reported at 48% confidence."""
        r = analyse({"CHOL": 310, "LDL": 95, "HDL": 26, "TRIG": 1450}, "M", 46)
        finding = r.findings["severe_hypertriglyceridemia"]
        assert finding["severity"] == "critical"
        assert finding["final_confidence"] >= 0.75
        assert r.findings["atherogenic_dyslipidemia"]["severity"] != "critical"

    def test_hdl_limit_is_sex_specific(self):
        values = {"CHOL": 180, "LDL": 95, "HDL": 45, "TRIG": 120}
        assert analyse(values, "F").by_name["Low HDL cholesterol"]["severity"] == "routine"
        assert analyse(values, "M").findings == {}

    @pytest.mark.parametrize("ldl,expected", [(189, False), (190, True)])
    def test_familial_hypercholesterolaemia_threshold(self, ldl, expected):
        r = analyse({"CHOL": 270, "LDL": ldl, "HDL": 50, "TRIG": 150}, "M", 40)
        assert ("familial_hypercholesterolemia_suspected" in r.findings) is expected
        assert r.findings["hypercholesterolemia"]["severity"] == "routine"   # CHOL 270

    def test_lipid_profile_without_ldl_is_still_analysed(self):
        """Labs omit a calculated LDL above TG 400; the whole report used to be refused."""
        r = analyse({"CHOL": 260, "HDL": 34, "TRIG": 700}, "M", 52)
        assert r.raw["status"] == "partial"
        assert any("LDL" in message for message in r.raw["error_messages"])
        assert "severe_hypertriglyceridemia" in r.findings

    def test_a_single_stray_value_is_still_refused(self):
        assert analyse({"HGB": 6.2}).raw["status"] == "error"


# ── Presentation ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("values,gender", [
    ({"CHOL": 205, "LDL": 118, "HDL": 32, "TRIG": 290}, "M"),
    ({"HGB": 8.9, "MCV": 112, "MCH": 36, "RDW": 16.0, "WBC": 3.6, "NEUT": 55, "LYMPH": 38,
      "PLT": 130}, "M"),
    ({"ALT": 48, "AST": 72, "ALP": 130, "TBIL": 2.2, "DBIL": 0.9, "TP": 7.0, "ALB": 2.6,
      "GLOB": 4.4, "AGR": 0.59}, "M"),
])
def test_each_action_is_recommended_once(values, gender):
    """"Iron studies" used to appear six ways and "cardiovascular risk" eight."""
    recs = analyse(values, gender).recommendations
    ids = [rec["recommendation_id"] for rec in recs]
    names = [rec["recommendation_name"].lower() for rec in recs]
    assert len(ids) == len(set(ids))
    assert len(names) == len(set(names))


def test_a_layer2_failure_explains_itself():
    """A database outage used to surface only as "layer2: no biomarkers normalized"."""
    class _BrokenNormalizer:
        validation_issues = []

        async def normalize(self, extracted, metadata):
            self.validation_issues = [
                f"Failed to normalize {b['name']!r}: Database error during reference-range lookup"
                for b in extracted
            ]
            return []

    orchestrator = CBCOrchestrator(_BrokenNormalizer(), FeatureGenerator(merged_feature_registry()),
                                   CompositeReasoner(), None)
    result = asyncio.run(orchestrator.analyze_cbc(
        {"HGB": 6.8, "MCV": 72, "PLT": 48, "RDW": 21.5, "WBC": 18.7}, "p", gender="F", age_years=62,
    ))
    assert result.status == "error"
    assert "Database error during reference-range lookup" in result.error_messages[-1]
