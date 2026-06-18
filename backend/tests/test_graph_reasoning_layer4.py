"""
Tests for Layer 4 (Graph Reasoning) — reasoning over the EXISTING CBC graph.

Covers:
  - TestGraphReasoningEngine   reason() traverses Biomarker -> Disease/Finding (full CBC)
  - TestBuildObservations      facts + values/units -> biomarker observations
  - TestFailureIsolation       a failing Cypher query degrades to 'partial'
  - TestBiomarkerMapping       fact <-> code <-> graph-name(s) bridging

No real Neo4j is needed — FakeNeo4j emulates ``Neo4jConnection.query`` by
inspecting the Cypher and applying a tiny in-memory CBC graph keyed by
(biomarker code, direction). The directional/threshold gating itself is Cypher
and is verified separately against the live graph.
"""

import pytest

from models.feature_schemas import GeneratedFeature, Layer3Output
from models.normalization_schemas import NormalizedBiomarker
from services.graph_reasoning.biomarker_mappings import BiomarkerFactMapping
from services.graph_reasoning.reasoning_engine import GraphReasoningEngine


# ── Builders ────────────────────────────────────────────────────────────────
def _nb(code, name, value, unit, status, loinc=None):
    return NormalizedBiomarker(
        biomarker_id=code, biomarker_name=name, value=value, unit=unit,
        reference_min=0.0, reference_max=0.0, gender="F", age_group="adult",
        lab_source="Any", status=status, deviation_from_min=0.0, deviation_percent=0.0,
        extraction_confidence=0.95, loinc_code=loinc,
    )


def _feat(fid, ftype, value, source=None):
    return GeneratedFeature(
        feature_id=fid, feature_name=fid, feature_type=ftype, value=value,
        source_biomarkers=[source] if source else [],
    )


def _ida_panel():
    """Iron-deficiency CBC: low HGB/HCT/MCV/MCH/MCHC, high RDW, high PLT."""
    return Layer3Output(
        normalized_biomarkers=[
            _nb("HGB", "Hemoglobin", 10.2, "g/dL", "LOW", loinc="718-7"),
            _nb("MCV", "MCV", 72.0, "fL", "LOW", loinc="787-2"),
            _nb("MCH", "MCH", 24.0, "pg", "LOW"),
            _nb("MCHC", "MCHC", 30.0, "g/dL", "LOW"),
            _nb("RDW", "RDW", 16.5, "%", "HIGH"),
            _nb("PLT", "Platelets", 450.0, "K/uL", "HIGH"),
        ],
        generated_features=[
            _feat("hemoglobin_low", "BINARY", True, "HGB"),
            _feat("mcv_low", "BINARY", True, "MCV"),
            _feat("mch_low", "BINARY", True, "MCH"),
            _feat("mchc_low", "BINARY", True, "MCHC"),
            _feat("rdw_high", "BINARY", True, "RDW"),
            _feat("platelets_high", "BINARY", True, "PLT"),
            _feat("rbc_index", "RATIO", 2.3),  # not a directional biomarker fact
        ],
    )


# ── Fake Neo4j: emulates query() with a tiny CBC graph ──────────────────────
# (code, direction) -> [(finding_id, finding_name, kind, threshold, unit, operator)]
_GRAPH = {
    ("HGB", "low"): [("disease::anaemia", "anaemia", "Disease", 110.0, "g/l", "<")],
    ("HGB", "high"): [("disease::no anaemia", "No anaemia", "Disease", 120.0, "g/l", ">=")],
    ("MCV", "low"): [("finding::iron deficiency", "iron deficiency", "Finding", None, None, "")],
    ("MCH", "low"): [("finding::iron deficiency", "iron deficiency", "Finding", None, None, "")],
    ("MCHC", "low"): [("finding::iron deficiency", "iron deficiency", "Finding", None, None, ""),
                      ("disease::anemia", "anemia", "Disease", None, None, "")],
    ("RDW", "high"): [("finding::anisocytosis", "anisocytosis", "Finding", None, None, ""),
                      ("disease::anemia", "anemia", "Disease", None, None, "")],
    ("PLT", "high"): [("finding::thrombocytosis", "Thrombocytosis", "Finding", None, None, "")],
}
_RECS = {
    "disease::anaemia": [("recommendation::iron_studies", "Iron studies", "ACTION"),
                         ("followuptest::ferritin", "Ferritin test", "TEST")],
    "finding::iron deficiency": [("followuptest::ferritin", "Ferritin test", "TEST")],
}


class FakeNeo4j:
    def __init__(self, fail=None, conflicts=None):
        self._fail = fail or set()
        self._conflicts = conflicts or []
        self.calls = []

    async def query(self, cypher, params=None):
        params = params or {}
        if "HAS_THRESHOLD" in cypher:                 # QUERY_INFER_DISEASES
            self.calls.append("diseases")
            if "diseases" in self._fail:
                raise RuntimeError("neo4j down")
            rows = []
            for obs in params["observations"]:
                for fid, fname, kind, thr, unit, op in _GRAPH.get((obs["code"], obs["direction"]), []):
                    rows.append({
                        "finding_id": fid, "finding_name": fname, "kind": kind,
                        "biomarker": obs["code"], "biomarker_name": obs["code"],
                        "patient_value": obs["value"], "direction": obs["direction"],
                        "threshold_value": thr, "threshold_unit": unit, "operator": op,
                    })
            return rows
        if "Recommendation" in cypher:                # QUERY_INFER_RECOMMENDATIONS
            self.calls.append("recs")
            if "recs" in self._fail:
                raise RuntimeError("neo4j down")
            out = []
            for tid in params["target_ids"]:
                for rid, rname, rtype in _RECS.get(tid, []):
                    out.append({"target_id": tid, "recommendation_id": rid,
                                "recommendation_name": rname, "recommendation_type": rtype})
            return out
        if "CONTRADICTS" in cypher:                   # QUERY_DETECT_CONFLICTS
            self.calls.append("conflicts")
            if "conflicts" in self._fail:
                raise RuntimeError("neo4j down")
            return self._conflicts
        return []


# ── TestGraphReasoningEngine ────────────────────────────────────────────────
class TestGraphReasoningEngine:
    async def test_infers_across_full_cbc(self):
        out = await GraphReasoningEngine(FakeNeo4j()).reason(_ida_panel())
        assert out.status == "success"
        names = {f.finding_name for f in out.validated_findings}
        assert {"anaemia", "iron deficiency", "anemia", "anisocytosis", "Thrombocytosis"} <= names

    async def test_multibiomarker_raises_confidence(self):
        out = await GraphReasoningEngine(FakeNeo4j()).reason(_ida_panel())
        idef = next(f for f in out.validated_findings if f.finding_name == "iron deficiency")
        # MCV + MCH + MCHC = 3 biomarkers → 0.55 + 0.10*3 = 0.85
        assert {l.biomarker_id for l in idef.evidence_chain} == {"MCV", "MCH", "MCHC"}
        assert idef.confidence == pytest.approx(0.85, abs=1e-6)

    async def test_evidence_shows_threshold_and_units(self):
        out = await GraphReasoningEngine(FakeNeo4j()).reason(_ida_panel())
        anaemia = next(f for f in out.validated_findings if f.finding_name == "anaemia")
        narr = anaemia.evidence_chain[0].narrative
        assert "HGB=10.2 g/dL (LOW)" in narr and "[WHO threshold <110.0 g/l]" in narr

    async def test_direction_low_vs_high(self):
        low = await GraphReasoningEngine(FakeNeo4j()).reason(Layer3Output(
            normalized_biomarkers=[_nb("HGB", "Hemoglobin", 10.2, "g/dL", "LOW")],
            generated_features=[_feat("hemoglobin_low", "BINARY", True, "HGB")]))
        high = await GraphReasoningEngine(FakeNeo4j()).reason(Layer3Output(
            normalized_biomarkers=[_nb("HGB", "Hemoglobin", 18.0, "g/dL", "HIGH")],
            generated_features=[_feat("hemoglobin_high", "BINARY", True, "HGB")]))
        assert {f.finding_name for f in low.validated_findings} == {"anaemia"}
        assert {f.finding_name for f in high.validated_findings} == {"No anaemia"}

    async def test_recommendations_built(self):
        out = await GraphReasoningEngine(FakeNeo4j()).reason(_ida_panel())
        rec_ids = {r.recommendation_id for r in out.recommendations}
        assert "recommendation::iron_studies" in rec_ids and "followuptest::ferritin" in rec_ids
        iron = next(r for r in out.recommendations if r.recommendation_id == "recommendation::iron_studies")
        assert iron.recommendation_type == "ACTION" and iron.from_finding == "disease::anaemia"

    async def test_audit_preserves_layer3_input(self):
        out = await GraphReasoningEngine(FakeNeo4j()).reason(_ida_panel())
        l3 = out.audit_trail.layer3_input
        assert "generated_features" in l3 and "normalized_biomarkers" in l3
        assert out.audit_trail.patterns_validated == 0
        assert "infer_diseases" in out.audit_trail.neo4j_queries_executed

    async def test_no_facts_no_findings(self):
        out = await GraphReasoningEngine(FakeNeo4j()).reason(
            Layer3Output(normalized_biomarkers=[], generated_features=[]))
        assert out.validated_findings == [] and out.status == "no_findings"

    async def test_conflicts_mapped(self):
        conflict = {"disease1_id": "disease::a", "disease1_name": "A",
                    "disease2_id": "disease::b", "disease2_name": "B",
                    "conflict_type": "CONTRADICTS", "severity": "high", "conflict_reason": "x"}
        out = await GraphReasoningEngine(FakeNeo4j(conflicts=[conflict])).reason(_ida_panel())
        assert len(out.conflicts) == 1
        assert out.conflicts[0].conflict_severity == "high"
        assert out.conflicts[0].recommendation == "MANUAL_REVIEW"


# ── TestBuildObservations ───────────────────────────────────────────────────
class TestBuildObservations:
    def test_binary_facts_become_observations(self):
        engine = GraphReasoningEngine(FakeNeo4j())
        l3 = _ida_panel()
        obs = engine._build_observations(l3, l3.normalized_biomarkers)
        by_code = {o["code"]: o for o in obs}
        assert {"HGB", "MCV", "MCH", "MCHC", "RDW", "PLT"} == set(by_code)  # ratio excluded
        assert by_code["HGB"]["direction"] == "low" and by_code["HGB"]["unit"] == "g/dL"
        assert "hemoglobin" in by_code["HGB"]["names"] and "haemoglobin" in by_code["HGB"]["names"]
        assert by_code["HGB"]["loinc"] == "718-7"   # LOINC join key carried through
        assert by_code["MCV"]["loinc"] == "787-2"
        assert by_code["RDW"]["loinc"] == ""        # not LOINC-tagged here → name fallback
        assert by_code["RDW"]["direction"] == "high"

    def test_false_binary_and_missing_value_excluded(self):
        engine = GraphReasoningEngine(FakeNeo4j())
        l3 = Layer3Output(
            normalized_biomarkers=[_nb("HGB", "Hemoglobin", 10.0, "g/dL", "LOW")],
            generated_features=[
                _feat("hemoglobin_low", "BINARY", False, "HGB"),  # value False
                _feat("mcv_low", "BINARY", True, "MCV"),          # MCV not in biomarkers
            ],
        )
        assert engine._build_observations(l3, l3.normalized_biomarkers) == []


# ── TestFailureIsolation ────────────────────────────────────────────────────
class TestFailureIsolation:
    async def test_recommendation_failure_degrades_to_partial(self):
        out = await GraphReasoningEngine(FakeNeo4j(fail={"recs"})).reason(_ida_panel())
        assert out.status == "partial"
        assert any("recommendations" in e for e in out.error_messages)
        assert len(out.validated_findings) >= 1  # findings still produced

    async def test_disease_failure_degrades_to_partial(self):
        out = await GraphReasoningEngine(FakeNeo4j(fail={"diseases"})).reason(_ida_panel())
        assert out.status == "partial" and out.validated_findings == []


# ── TestBiomarkerMapping ────────────────────────────────────────────────────
class TestBiomarkerMapping:
    def test_fact_to_biomarker(self):
        m = BiomarkerFactMapping()
        assert m.get_biomarker_for_fact("hemoglobin_low") == "HGB"
        assert m.get_biomarker_for_fact("mentzer_index") == ["MCV", "RBC"]
        assert m.get_biomarker_for_fact("unknown") is None

    def test_graph_names_and_ids(self):
        m = BiomarkerFactMapping()
        assert "hemoglobin" in m.graph_names_for_code("HGB")
        assert "haemoglobin" in m.graph_names_for_code("HGB")
        assert "rcdw" in m.graph_names_for_code("RDW")
        assert "biomarker::hemoglobin" in m.graph_ids_for_code("HGB")

    def test_direction(self):
        m = BiomarkerFactMapping()
        assert m.direction_for_fact("rdw_high") == "high"
        assert m.direction_for_fact("mcv_low") == "low"
        assert m.direction_for_fact("mentzer_index") is None
