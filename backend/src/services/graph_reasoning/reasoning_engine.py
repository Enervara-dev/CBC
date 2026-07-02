"""
Graph reasoning engine (Layer 4) — reasons over the EXISTING CBC knowledge graph.

Layer 3 emits facts (``hemoglobin_low``, ``mcv_low``, ``rdw_high`` …); this engine
infers diseases/findings by traversing the live clinical knowledge graph directly
— no InferenceRule nodes, no hardcoded disease patterns. The graph is a full CBC +
iron-studies knowledge base, so inference spans many biomarkers:

    Biomarker (HGB)  -[HAS_THRESHOLD]-> Threshold -[INDICATES]-> Disease (anaemia)
    Biomarker (MCV)  -[INDICATES]----------------------------->  Finding (iron deficiency)
    Biomarker (RDW)  -[INDICATES]----------------------------->  Finding (anisocytosis)
    Biomarker (ferritin) -[ASSOCIATED_WITH]------------------->  Disease (iron deficiency anaemia)
                     ... -[REQUIRES_TEST]-> FollowUpTest / <-[INDICATES]- Recommendation

Traversal (per fact's biomarker):
  - ``HAS_THRESHOLD -> Threshold -> INDICATES`` (direction-gated by the threshold's
    ``operator`` vs the fact's low/high — Layer 2 already decided abnormality vs the
    reference range, so we avoid unit-fragile value/threshold comparisons),
  - direct ``INDICATES`` to a Disease/Finding (the diagnostic edge), and
  - ``ASSOCIATED_WITH`` to a Disease (disease-level associations, e.g. iron-deficiency anaemia).

Each step is isolated: a failure is logged, recorded in ``error_messages``, and the
run continues (status ``"partial"``). ``detected_patterns`` is not consumed.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from models.feature_schemas import Layer3Output
from models.graph_schemas import (
    AuditTrail,
    ConflictAlert,
    EvidenceLink,
    Layer4Output,
    Recommendation,
    ValidatedFinding,
)
from models.normalization_schemas import NormalizedBiomarker
from services.graph_reasoning.biomarker_mappings import BiomarkerFactMapping
from services.graph_reasoning.contract_compat import check_compatibility
from services.graph_reasoning.graph_contract import GraphContract, load_contract
from services.graph_reasoning.graph_diagnostics import GraphDiagnostics
from services.graph_reasoning.neo4j_connection import Neo4jConnection
from services.graph_reasoning.traversal_builder import TraversalQueryBuilder

_CONFLICT_RECOMMENDATION = {
    "high": "MANUAL_REVIEW",
    "medium": "NOTE_CLINICIAN",
    "low": "INFORMATIONAL",
}
_URGENCY_RANK = {"stat": 3, "urgent": 2, "routine": 1}
_MAX_RECOMMENDATIONS = 12


# ── Cypher is now generated from the Graph Contract (see traversal_builder.py). ──
# The reasoning engine no longer hard-codes labels, relationship types, traversal
# patterns, or property names — they are all derived from graph_contract.yaml.


class GraphReasoningEngine:
    """Infers diseases/findings by traversing the Neo4j knowledge graph.

    Traversal behaviour is **contract-driven**: at construction the engine loads
    the Graph Contract, fails fast if it is incompatible, and generates its Cypher
    from the contract's ``traversal_patterns``. The clinical reasoning algorithms
    (observation building, confidence, dedup, evidence, conflicts) are unchanged.
    """

    def __init__(
        self,
        neo4j_connection: Neo4jConnection,
        logger: Optional[logging.Logger] = None,
        contract: Optional[GraphContract] = None,
        query_builder: Optional[TraversalQueryBuilder] = None,
    ) -> None:
        self.neo4j = neo4j_connection
        self.logger = logger or logging.getLogger(__name__)
        self.mapping = BiomarkerFactMapping()
        self.metrics: Dict[str, int] = {"nodes_queried": 0}

        # ── Contract loading + fail-fast compatibility check (requirements 1/3) ──
        self.contract = contract or load_contract()
        self.contract_warnings = check_compatibility(self.contract)
        for w in self.contract_warnings:
            self.logger.warning("Graph Contract warning: %s", w)

        # ── Traversal generation from the contract (requirement 2) ──────────────
        self.builder = query_builder or TraversalQueryBuilder(self.contract)
        self._q_diseases = self.builder.infer_diseases()
        self._q_recommendations = self.builder.infer_recommendations()
        self._q_conflicts = self.builder.detect_conflicts()  # None if pattern absent

        self.diagnostics = GraphDiagnostics(neo4j_connection, self.contract, self.builder)
        self.logger.info(
            "Layer 4 engine initialised against Graph Contract v%s (%s).",
            self.contract.version, self.contract.source_path,
        )

    # ── Main orchestration ───────────────────────────────────────────────────
    async def reason(self, layer3_output: Layer3Output) -> Layer4Output:
        """Traverse the CBC graph from the patient's facts and assemble Layer4Output."""
        start = datetime.now(timezone.utc)
        run_start = time.perf_counter()
        self.metrics = {"nodes_queried": 0}
        errors: List[str] = []
        queries_executed: List[str] = []

        biomarkers = layer3_output.normalized_biomarkers
        observations = self._build_observations(layer3_output, biomarkers)
        self.logger.info(
            "Graph reasoning started: %d biomarker observations, %d biomarkers",
            len(observations), len(biomarkers),
        )

        # STEP 1 — infer diseases/findings from the abnormal biomarkers
        unique: Dict[str, Dict[str, Any]] = {}
        try:
            if observations:
                rows = await self.neo4j.query(self._q_diseases, {"observations": observations})
                self.metrics["nodes_queried"] += len(rows)
                unique = self._aggregate_findings(rows)
                queries_executed.append("infer_diseases")
            self.logger.info("STEP 1 complete: %d disease/finding(s) inferred", len(unique))
        except Exception as exc:
            self.logger.error("STEP 1 (infer diseases) failed: %s", exc)
            errors.append(f"infer_diseases: {exc}")

        validated_findings = self._build_findings(unique, biomarkers)
        target_ids = list(unique.keys())

        # STEP 3 — recommendations / follow-up tests for the inferred targets
        recommendations: List[Recommendation] = []
        try:
            if target_ids:
                rec_rows = await self.neo4j.query(
                    self._q_recommendations, {"target_ids": target_ids}
                )
                self.metrics["nodes_queried"] += len(rec_rows)
                recommendations = self._build_recommendations(rec_rows)
                queries_executed.append("infer_recommendations")
            self.logger.info("STEP 3 complete: %d recommendation(s)", len(recommendations))
        except Exception as exc:
            self.logger.error("STEP 3 (recommendations) failed: %s", exc)
            errors.append(f"recommendations: {exc}")

        # STEP 4 — detect conflicts among inferred targets (only if the contract
        # declares the disease_contradiction traversal; otherwise skip cleanly).
        conflicts: List[ConflictAlert] = []
        try:
            if self._q_conflicts is not None and len(target_ids) > 1:
                conflict_rows = await self.neo4j.query(
                    self._q_conflicts, {"target_ids": target_ids}
                )
                self.metrics["nodes_queried"] += len(conflict_rows)
                conflicts = self._build_conflicts(conflict_rows)
                queries_executed.append("detect_conflicts")
            self.logger.info("STEP 4 complete: %d conflict(s) detected", len(conflicts))
        except Exception as exc:
            self.logger.error("STEP 4 (detect conflicts) failed: %s", exc)
            errors.append(f"detect_conflicts: {exc}")

        # STEP 4b — diagnostics: if STEP 1 ran cleanly but produced nothing, explain
        # why (empty graph / contract violation / missing traversal / unmatched
        # biomarkers / genuine absence) instead of returning a silent empty result.
        diagnostics: List[str] = []
        step1_failed = any(e.startswith("infer_diseases") for e in errors)
        if not validated_findings and not step1_failed:
            try:
                diagnostics = await self.diagnostics.diagnose(observations)
                for d in diagnostics:
                    self.logger.info("Layer 4 diagnostic — %s", d)
            except Exception as exc:  # noqa: BLE001 — diagnostics never break reasoning
                self.logger.warning("Diagnostics failed: %s", exc)

        # STEP 5 — audit trail
        execution_time_ms = round((time.perf_counter() - run_start) * 1000.0, 3)
        audit_trail = AuditTrail(
            timestamp=start.isoformat(),
            layer3_input=layer3_output.model_dump(),
            neo4j_queries_executed=queries_executed,
            nodes_queried=self.metrics["nodes_queried"],
            patterns_validated=0,
            findings_extracted=len(validated_findings),
            conflicts_detected=len(conflicts),
            recommendations_generated=len(recommendations),
            execution_time_ms=execution_time_ms,
        )
        self.logger.info("Graph reasoning finished in %.3f ms", execution_time_ms)

        if errors:
            status = "partial"
        elif validated_findings:
            status = "success"
        else:
            status = "no_findings"

        return Layer4Output(
            validated_findings=validated_findings,
            recommendations=recommendations,
            conflicts=conflicts,
            audit_trail=audit_trail,
            status=status,
            error_messages=errors,
            diagnostics=diagnostics,
        )

    # ── Inputs ───────────────────────────────────────────────────────────────
    def _build_observations(
        self, layer3_output: Layer3Output, biomarkers: List[NormalizedBiomarker]
    ) -> List[Dict[str, Any]]:
        """Active binary facts → {code, names[], ids[], value, unit, loinc, direction} rows."""
        values = {b.biomarker_id: b.value for b in biomarkers}
        units = {b.biomarker_id: (b.unit or "") for b in biomarkers}
        loincs = {b.biomarker_id: (getattr(b, "loinc_code", None) or "") for b in biomarkers}
        observations: List[Dict[str, Any]] = []
        seen: set = set()
        for f in layer3_output.generated_features:
            ftype = str(getattr(f.feature_type, "value", f.feature_type))
            if ftype != "BINARY" or not bool(f.value):
                continue
            direction = self.mapping.direction_for_fact(f.feature_id)
            code = self.mapping.get_biomarker_for_fact(f.feature_id)
            if direction is None or not isinstance(code, str):
                continue
            value = values.get(code)
            if value is None or code in seen:
                continue
            seen.add(code)
            observations.append({
                "code": code,
                "names": self.mapping.graph_names_for_code(code),
                "ids": self.mapping.graph_ids_for_code(code),
                "value": float(value),
                "unit": units.get(code, ""),
                "loinc": loincs.get(code, ""),
                "direction": direction,
            })
        return observations

    # ── Result assembly ──────────────────────────────────────────────────────
    @staticmethod
    def _aggregate_findings(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """De-duplicate inferred targets; accumulate one evidence row per biomarker."""
        unique: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            fid = row.get("finding_id")
            if not fid:
                continue
            entry = unique.setdefault(fid, {
                "finding_id": fid,
                "finding_name": row.get("finding_name", fid),
                "kind": row.get("kind"),
                "evidence": [],
                "_seen_biomarkers": set(),
            })
            code = row.get("biomarker")
            if code in entry["_seen_biomarkers"]:
                continue
            entry["_seen_biomarkers"].add(code)
            entry["evidence"].append({
                "biomarker": code,
                "biomarker_name": row.get("biomarker_name", code),
                "direction": row.get("direction"),
                "patient_value": row.get("patient_value"),
                "threshold_value": row.get("threshold_value"),
                "threshold_unit": row.get("threshold_unit"),
                "operator": row.get("operator"),
            })
        return unique

    def _build_findings(
        self, unique: Dict[str, Dict[str, Any]], biomarkers: List[NormalizedBiomarker]
    ) -> List[ValidatedFinding]:
        """ValidatedFinding per inferred target, with confidence + evidence chain."""
        by_code = {b.biomarker_id: b for b in biomarkers}
        findings: List[ValidatedFinding] = []
        for fid, info in unique.items():
            evidence = info["evidence"]
            # more corroborating biomarkers → higher confidence
            confidence = round(min(0.95, 0.55 + 0.10 * len(evidence)), 4)
            links = [
                EvidenceLink(
                    biomarker_id=ev.get("biomarker") or "",
                    biomarker_name=ev.get("biomarker_name") or ev.get("biomarker") or "",
                    evidence_strength=confidence,
                    narrative=self._narrative(ev, info["finding_name"], by_code),
                )
                for ev in evidence
            ]
            findings.append(ValidatedFinding(
                finding_id=fid,
                finding_name=info["finding_name"],
                confidence=confidence,
                severity=None,
                evidence_chain=links,
                source_pattern=f"knowledge_graph:{info.get('kind') or 'Disease'}",
            ))
        return findings

    @staticmethod
    def _narrative(evidence: Dict[str, Any], finding_name: str, by_code: Dict[str, Any]) -> str:
        """e.g. ``"HGB=10.2 g/dL (LOW) supports anaemia [WHO threshold <110 g/L]"``."""
        code = evidence.get("biomarker", "?")
        nb = by_code.get(code)
        value = evidence.get("patient_value")
        unit = (getattr(nb, "unit", "") or "").strip()
        status = getattr(nb, "status", None)
        status = getattr(status, "value", status) or (evidence.get("direction") or "").upper()
        head = f"{code}={value}{(' ' + unit) if unit else ''} ({status}) supports {finding_name}"
        thr = evidence.get("threshold_value")
        if thr is not None:
            op = evidence.get("operator") or ""
            tu = (evidence.get("threshold_unit") or "").strip()
            return f"{head} [WHO threshold {op}{thr}{(' ' + tu) if tu else ''}]"
        return head

    def _build_recommendations(self, rows: List[Dict[str, Any]]) -> List[Recommendation]:
        """Dedup by id; ACTIONs before TESTs; cap the list."""
        unique: Dict[str, Recommendation] = {}
        for raw in rows:
            rec_id = raw.get("recommendation_id")
            if not rec_id or rec_id in unique:
                continue
            rtype = raw.get("recommendation_type", "ACTION")
            unique[rec_id] = Recommendation(
                recommendation_id=rec_id,
                recommendation_name=raw.get("recommendation_name", ""),
                recommendation_type=rtype if rtype in ("TEST", "REFERRAL", "MONITOR", "ACTION") else "ACTION",
                priority=int(raw.get("priority", 5) or 5),
                urgency=raw.get("urgency", "routine") or "routine",
                from_finding=raw.get("target_id", ""),
            )
        ordered = sorted(
            unique.values(),
            key=lambda r: (r.priority, 0 if r.recommendation_type == "ACTION" else 1, r.recommendation_name),
        )
        return ordered[:_MAX_RECOMMENDATIONS]

    @staticmethod
    def _build_conflicts(rows: List[Dict[str, Any]]) -> List[ConflictAlert]:
        """Map contradictory-disease rows to ConflictAlerts."""
        conflicts: List[ConflictAlert] = []
        for c in rows:
            severity = str(c.get("severity", "medium")).lower()
            conflicts.append(ConflictAlert(
                finding1_id=c.get("disease1_id", ""),
                finding1_name=c.get("disease1_name", ""),
                finding2_id=c.get("disease2_id", ""),
                finding2_name=c.get("disease2_name", ""),
                conflict_severity=severity,
                recommendation=_CONFLICT_RECOMMENDATION.get(severity, "INFORMATIONAL"),
                note=c.get("conflict_reason", ""),
            ))
        return conflicts
