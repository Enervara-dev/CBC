"""
Traversal query generation (Layer 4).

Builds the Cypher the reasoning engine runs **from the Graph Contract's
``traversal_patterns``** — every label, relationship type and property name is
looked up on the contract, never hard-coded here. Kept separate from the
reasoning logic (requirement 5): this module only turns contract patterns into
Cypher; the clinical gating (operator allow-lists, unit reconciliation,
confidence, dedup) stays in the engine / is passed in verbatim.

The generated queries are structurally identical to the previous hard-coded ones,
so the reasoning output is unchanged against a compatible graph — only the
schema identifiers now come from the contract.
"""

from __future__ import annotations

from typing import List

from services.graph_reasoning.graph_contract import GraphContract

# Directional operator allow-lists — these are *reasoning* logic (how a low/high
# fact maps to a comparison), not graph schema, so they live with the engine.
LOW_OPERATORS = ["<", "<=", "lt", "le", "below", "less"]
HIGH_OPERATORS = [">", ">=", "gt", "ge", "above", "greater"]


def _cypher_list(values: List[str]) -> str:
    """Render a Python list of operators as a Cypher string list literal."""
    return "[" + ",".join(f"'{v}'" for v in values) + "]"


class TraversalQueryBuilder:
    """Generate the three Layer-4 Cypher queries from a :class:`GraphContract`."""

    def __init__(self, contract: GraphContract) -> None:
        self.c = contract
        # ── contract-derived identifiers (resolved once) ──────────────────────
        self.BIO = contract.label("Biomarker")
        self.THR = contract.label("Threshold")
        self.DIS = contract.label("Disease")
        self.FIN = contract.label("Finding")
        self.REC = contract.label("Recommendation")
        self.FUT = contract.label("FollowUpTest")

        self.id_p = contract.id_property()
        self.name_p = contract.node_property("Biomarker", "name")
        self.loinc_p = contract.index_property("biomarker_loinc") \
            or contract.node_property("Biomarker", "loinc_code")
        self.op_p = contract.node_property("Threshold", "operator")
        self.val_p = contract.node_property("Threshold", "value")
        self.unit_p = contract.node_property("Threshold", "unit")

        # ── relationship types per traversal pattern (derived) ────────────────
        self.rel_bt = self._rel("biomarker_threshold")
        self.rel_ti = self._rel("threshold_indicates")
        self.rel_bi = self._rel("biomarker_indicates")
        self.rel_ba = self._rel("biomarker_associated")
        self.rel_ro = self._rel("recommendation_outcome")
        self.rel_of = self._rel("outcome_followup")

        # ── label predicates per traversal target set (derived) ───────────────
        self.ti_to = self._label_pred("d", self.c.traversal("threshold_indicates").to_labels)
        self.bi_to = self._label_pred("d", self.c.traversal("biomarker_indicates").to_labels)
        self.ba_to = self._labels(self.c.traversal("biomarker_associated").to_labels)  # inline on node
        # outcome set = anything a recommendation/follow-up traversal touches
        outcome_labels = sorted(set(self.c.traversal("recommendation_outcome").to_labels)
                                | set(self.c.traversal("outcome_followup").from_labels))
        self.outcome_pred = self._label_pred("d", outcome_labels)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _rel(self, pattern_id: str) -> str:
        """`HAS_THRESHOLD` or `INDICATES|ASSOCIATED_WITH` — from the contract."""
        return "|".join(self.c.rels_for(pattern_id))

    def _label_pred(self, var: str, labels: List[str]) -> str:
        """``(d:Disease OR d:Finding)`` from a contract label list."""
        mapped = [self.c.label(lbl) for lbl in labels]
        return "(" + " OR ".join(f"{var}:{lbl}" for lbl in mapped) + ")"

    def _labels(self, labels: List[str]) -> str:
        """``Disease`` / ``Disease|Finding`` for inline node-pattern labels."""
        return ":".join(self.c.label(lbl) for lbl in labels) if len(labels) == 1 \
            else "|".join(self.c.label(lbl) for lbl in labels)

    # ── Query A — infer diseases/findings ─────────────────────────────────────
    def infer_diseases(self) -> str:
        low = _cypher_list(LOW_OPERATORS)
        high = _cypher_list(HIGH_OPERATORS)
        return f"""
UNWIND $observations AS obs
CALL (obs) {{
    MATCH (b:{self.BIO})
    WHERE (obs.loinc <> '' AND b.{self.loinc_p} = obs.loinc)
       OR toLower(b.{self.name_p}) IN obs.names OR toLower(b.{self.id_p}) IN obs.ids
    OPTIONAL MATCH (b)-[:{self.rel_bt}]->(:{self.THR})-[:{self.rel_ti}]->(dd:{self.DIS})
    RETURN count(dd) AS n_thresh
}}
MATCH (b:{self.BIO})
WHERE (obs.loinc <> '' AND b.{self.loinc_p} = obs.loinc)
   OR toLower(b.{self.name_p}) IN obs.names OR toLower(b.{self.id_p}) IN obs.ids
CALL (obs, b, n_thresh) {{
    // (A) threshold-modeled biomarkers: direction + operator + numeric value gate.
    MATCH (b)-[:{self.rel_bt}]->(t:{self.THR})-[:{self.rel_ti}]->(d)
    WHERE {self.ti_to} AND toFloat(t.{self.val_p}) IS NOT NULL
    WITH obs, d, t,
         toFloat(t.{self.val_p}) AS thresh,
         toLower(coalesce(t.{self.op_p}, '')) AS op,
         toLower(trim(coalesce(t.{self.unit_p}, '')))   AS tu,
         toLower(trim(coalesce(obs.unit, ''))) AS pu,
         obs.value AS raw_pval
    WITH obs, d, t, thresh, op,
         CASE
           WHEN tu = 'g/l'  AND pu = 'g/dl' THEN raw_pval * 10.0
           WHEN tu = 'g/dl' AND pu = 'g/l'  THEN raw_pval / 10.0
           ELSE raw_pval
         END AS pval
    WHERE (obs.direction = 'low'  AND op IN {low}  AND pval < thresh)
       OR (obs.direction = 'high' AND op IN {high} AND pval > thresh)
    RETURN d AS d, t AS t
    UNION
    // (B) non-threshold biomarkers: direct diagnostic edge (gated to n_thresh = 0).
    MATCH (b)-[:{self.rel_bi}]->(d)
    WHERE {self.bi_to} AND n_thresh = 0
    RETURN d AS d, null AS t
    UNION
    // (C) disease-level associations for non-threshold biomarkers.
    MATCH (b)-[:{self.rel_ba}]->(d:{self.ba_to})
    WHERE n_thresh = 0
    RETURN d AS d, null AS t
}}
WITH obs, b, d, t
RETURN DISTINCT
    d.{self.id_p}   AS finding_id,
    d.{self.name_p} AS finding_name,
    (CASE WHEN d:{self.DIS} THEN 'Disease' WHEN d:{self.FIN} THEN 'Finding' ELSE head(labels(d)) END) AS kind,
    obs.code  AS biomarker,
    b.{self.name_p}    AS biomarker_name,
    obs.value AS patient_value,
    obs.direction AS direction,
    toFloat(t.{self.val_p}) AS threshold_value,
    t.{self.unit_p} AS threshold_unit,
    toLower(coalesce(t.{self.op_p}, '')) AS operator
"""

    # ── Query B — recommendations + follow-up tests ───────────────────────────
    def infer_recommendations(self) -> str:
        return f"""
MATCH (d) WHERE d.{self.id_p} IN $target_ids AND {self.outcome_pred}
CALL (d) {{
    MATCH (d)<-[:{self.rel_ro}]-(r:{self.REC})
    RETURN r.{self.id_p} AS rid, r.{self.name_p} AS rname, 'ACTION' AS rtype
    UNION
    MATCH (d)-[:{self.rel_of}]->(ft:{self.FUT})
    RETURN ft.{self.id_p} AS rid, ft.{self.name_p} AS rname, 'TEST' AS rtype
}}
RETURN DISTINCT d.{self.id_p} AS target_id, rid AS recommendation_id,
       rname AS recommendation_name, rtype AS recommendation_type
"""

    # ── Query C — contradictory diseases (optional pattern) ───────────────────
    def detect_conflicts(self) -> str | None:
        if not self.c.has_traversal("disease_contradiction"):
            return None
        rel_dc = self._rel("disease_contradiction")
        d1 = self._label_pred("d1", self.c.traversal("disease_contradiction").from_labels)
        d2 = self._label_pred("d2", self.c.traversal("disease_contradiction").to_labels)
        return f"""
MATCH (d1)-[c:{rel_dc}]-(d2)
WHERE d1.{self.id_p} IN $target_ids AND d2.{self.id_p} IN $target_ids AND d1.{self.id_p} < d2.{self.id_p}
  AND {d1} AND {d2}
RETURN d1.{self.id_p} AS disease1_id, d1.{self.name_p} AS disease1_name,
       d2.{self.id_p} AS disease2_id, d2.{self.name_p} AS disease2_name,
       type(c) AS conflict_type,
       coalesce(c.severity, 'medium') AS severity,
       coalesce(c.reason, c.note, d1.{self.name_p} + ' contradicts ' + d2.{self.name_p}) AS conflict_reason
"""
