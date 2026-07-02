"""
Tests for the contract-driven Layer 4 refactor.

  - TestContractLoading      load_contract parses the bundled contract
  - TestCompatibility        version / traversal / capability checks + fail-fast
  - TestTraversalBuilder     Cypher is generated from the contract (not hard-coded)
  - TestEngineContractWiring engine loads the contract and fails fast if incompatible
  - TestDiagnostics          zero-result causes are classified explicitly
"""

import dataclasses

import pytest

from models.feature_schemas import Layer3Output
from services.graph_reasoning.graph_contract import (
    load_contract, GraphContract, GraphContractError, TraversalPattern,
)
from services.graph_reasoning.contract_compat import (
    check_compatibility, ContractCompatibilityError,
)
from services.graph_reasoning.traversal_builder import TraversalQueryBuilder
from services.graph_reasoning.graph_diagnostics import GraphDiagnostics
from services.graph_reasoning.reasoning_engine import GraphReasoningEngine


CONTRACT = load_contract()


class _FakeConn:
    """Minimal Neo4jConnection stand-in: count queries → 0 unless overridden."""

    def __init__(self, count_fn=None, rows=None):
        self._count_fn = count_fn
        self._rows = rows if rows is not None else []

    async def query(self, cypher, params=None):
        if self._count_fn is not None and "count(" in cypher:
            return [{"c": self._count_fn(cypher, params or {})}]
        return self._rows


class TestContractLoading:
    def test_loads_bundled_contract(self):
        assert CONTRACT.version == "1.0.0" and CONTRACT.major_version == 1
        assert CONTRACT.umbrella_label == "Entity"

    def test_label_and_rels_from_contract(self):
        assert CONTRACT.label("Biomarker") == "Biomarker"
        assert CONTRACT.rels_for("recommendation_outcome") == ["INDICATES", "ASSOCIATED_WITH"]
        assert CONTRACT.rels_for("biomarker_threshold") == ["HAS_THRESHOLD"]

    def test_join_keys_from_contract(self):
        assert CONTRACT.id_property() == "id"
        assert CONTRACT.index_property("biomarker_loinc") == "loinc_code"
        assert CONTRACT.node_property("Threshold", "operator") == "operator"

    def test_missing_file_raises(self):
        with pytest.raises(GraphContractError):
            load_contract("does_not_exist.yaml")

    def test_undeclared_property_raises(self):
        with pytest.raises(GraphContractError):
            CONTRACT.node_property("Biomarker", "nonexistent_prop")


class TestCompatibility:
    def test_bundled_contract_is_compatible(self):
        assert check_compatibility(CONTRACT) == []  # no warnings

    def test_wrong_major_version_fails_fast(self):
        bad = dataclasses.replace(CONTRACT, version="2.0.0")
        with pytest.raises(ContractCompatibilityError) as e:
            check_compatibility(bad)
        assert "major version 2" in str(e.value)

    def test_missing_required_traversal_fails_fast(self):
        tps = {k: v for k, v in CONTRACT.traversal_patterns.items() if k != "biomarker_indicates"}
        bad = dataclasses.replace(CONTRACT, traversal_patterns=tps)
        with pytest.raises(ContractCompatibilityError) as e:
            check_compatibility(bad)
        assert "biomarker_indicates" in str(e.value)

    def test_optional_traversal_absence_is_warning(self):
        tps = {k: v for k, v in CONTRACT.traversal_patterns.items() if k != "disease_contradiction"}
        warnings = check_compatibility(dataclasses.replace(CONTRACT, traversal_patterns=tps))
        assert any("disease_contradiction" in w for w in warnings)


class TestTraversalBuilder:
    def test_queries_use_contract_relationships(self):
        b = TraversalQueryBuilder(CONTRACT)
        assert "HAS_THRESHOLD" in b.infer_diseases()
        assert "REQUIRES_TEST" in b.infer_recommendations()
        assert "CONTRADICTS" in b.detect_conflicts()

    def test_relationship_rename_flows_into_cypher(self):
        # Rename the biomarker_indicates relationship in a synthetic contract and
        # prove the generated Cypher follows — i.e. nothing is hard-coded.
        tps = dict(CONTRACT.traversal_patterns)
        tps["biomarker_indicates"] = TraversalPattern(
            id="biomarker_indicates", from_labels=["Biomarker"],
            rels=["POINTS_TO"], to_labels=["Disease", "Finding"],
        )
        # declare the rel so compat/capability passes
        rels = dict(CONTRACT.relationship_types)
        rels["POINTS_TO"] = {"name": "POINTS_TO", "from": ["Biomarker"], "to": ["Disease", "Finding"]}
        c = dataclasses.replace(CONTRACT, traversal_patterns=tps, relationship_types=rels)
        cypher = TraversalQueryBuilder(c).infer_diseases()
        assert "POINTS_TO" in cypher

    def test_optional_conflict_query_absent_when_pattern_missing(self):
        tps = {k: v for k, v in CONTRACT.traversal_patterns.items() if k != "disease_contradiction"}
        c = dataclasses.replace(CONTRACT, traversal_patterns=tps)
        assert TraversalQueryBuilder(c).detect_conflicts() is None


class TestEngineContractWiring:
    def test_engine_loads_default_contract(self):
        eng = GraphReasoningEngine(_FakeConn())
        assert eng.contract.version == "1.0.0"
        assert "HAS_THRESHOLD" in eng._q_diseases

    def test_engine_fails_fast_on_incompatible_contract(self):
        bad = dataclasses.replace(CONTRACT, version="9.0.0")
        with pytest.raises(ContractCompatibilityError):
            GraphReasoningEngine(_FakeConn(), contract=bad)

    async def test_empty_input_reports_genuine_absence(self):
        out = await GraphReasoningEngine(_FakeConn()).reason(
            Layer3Output(normalized_biomarkers=[], generated_features=[]))
        assert out.status == "no_findings"
        assert any("GENUINE_ABSENCE" in d for d in out.diagnostics)


class TestDiagnostics:
    def _diag(self, count_fn):
        b = TraversalQueryBuilder(CONTRACT)
        return GraphDiagnostics(_FakeConn(count_fn=count_fn), CONTRACT, b)

    async def test_empty_graph(self):
        d = self._diag(lambda cy, p: 0)  # every count is 0 → total 0
        msgs = await d.diagnose([{"code": "HGB", "loinc": "718-7", "names": ["hemoglobin"], "ids": []}])
        assert any("EMPTY_GRAPH" in m for m in msgs)

    async def test_genuine_absence(self):
        # everything present (count>0) except the "IS NULL" missing-property probe
        d = self._diag(lambda cy, p: 0 if "IS NULL" in cy else 5)
        msgs = await d.diagnose([{"code": "HGB", "loinc": "718-7", "names": ["hemoglobin"], "ids": []}])
        assert any("GENUINE_ABSENCE" in m for m in msgs)

    async def test_unmatched_biomarkers(self):
        # graph populated, but the patient's biomarkers don't match any node
        def counts(cy, p):
            if "IN $loincs" in cy:
                return 0            # matched biomarkers = 0
            if "IS NULL" in cy:
                return 0
            return 5
        msgs = await self._diag(counts).diagnose(
            [{"code": "HGB", "loinc": "718-7", "names": ["hemoglobin"], "ids": []}])
        assert any("UNMATCHED_BIOMARKERS" in m for m in msgs)
