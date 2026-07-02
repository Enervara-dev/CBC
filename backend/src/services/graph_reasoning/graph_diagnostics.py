"""
Zero-result diagnostics (Layer 4).

When a required traversal returns nothing, this module probes the live graph
(best-effort, read-only) to explain *why* — so an operator gets an explicit
message instead of a silent empty result. It distinguishes:

    EMPTY_GRAPH            the database has no nodes
    CONTRACT_VIOLATION    a required label / umbrella label is absent in the graph
    MISSING_TRAVERSAL     a required (source)-[rel]->(target) pattern has 0 edges
    MISSING_PROPERTIES    matched nodes lack contract properties the gate needs
    UNMATCHED_BIOMARKERS  the patient's biomarkers aren't in the graph (LOINC/name)
    GENUINE_ABSENCE       everything is present; there simply is no finding

(Incompatible contract version and missing traversal *patterns* are caught
earlier, at startup, by contract_compat.check_compatibility — see the engine.)

Every probe is wrapped so a diagnostics failure never affects reasoning.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from services.graph_reasoning.graph_contract import GraphContract
from services.graph_reasoning.traversal_builder import TraversalQueryBuilder

logger = logging.getLogger(__name__)


class GraphDiagnostics:
    """Read-only probes that classify an empty Layer-4 result."""

    def __init__(self, connection: Any, contract: GraphContract, builder: TraversalQueryBuilder) -> None:
        self.conn = connection
        self.c = contract
        self.b = builder

    async def _count(self, cypher: str, params: Dict[str, Any] | None = None) -> int:
        rows = await self.conn.query(cypher, params or {})
        if not rows:
            return 0
        first = rows[0]
        # tolerate {'c': n} or a single-value record
        return int(next(iter(first.values())) if isinstance(first, dict) else first)

    async def diagnose(self, observations: List[Dict[str, Any]]) -> List[str]:
        """Return explicit ``CATEGORY: message`` diagnostics for an empty result."""
        if not observations:
            return ["GENUINE_ABSENCE: no active biomarker facts were produced upstream "
                    "(Layer 3 emitted no low/high observations) — nothing to reason over."]

        out: List[str] = []
        umbrella = self.c.umbrella_label
        try:
            total = await self._count("MATCH (n) RETURN count(n) AS c")
            if total == 0:
                return ["EMPTY_GRAPH: the graph contains 0 nodes — load/seed the "
                        "knowledge graph before reasoning."]

            if await self._count(f"MATCH (n:{umbrella}) RETURN count(n) AS c") == 0:
                out.append(f"CONTRACT_VIOLATION: no nodes carry the umbrella label "
                           f"'{umbrella}' required by the contract.")
            if await self._count(f"MATCH (b:{self.b.BIO}) RETURN count(b) AS c") == 0:
                out.append(f"CONTRACT_VIOLATION: no ':{self.b.BIO}' nodes exist in the graph.")
                return out  # nothing else is meaningful

            # Do the patient's biomarkers exist in the graph at all?
            match_params = {
                "loincs": [o["loinc"] for o in observations if o.get("loinc")],
                "names": sorted({n for o in observations for n in o.get("names", [])}),
                "ids": sorted({i for o in observations for i in o.get("ids", [])}),
            }
            matched = await self._count(
                f"MATCH (b:{self.b.BIO}) "
                f"WHERE b.{self.b.loinc_p} IN $loincs "
                f"OR toLower(b.{self.b.name_p}) IN $names OR toLower(b.{self.b.id_p}) IN $ids "
                f"RETURN count(b) AS c", match_params)
            if matched == 0:
                out.append("UNMATCHED_BIOMARKERS: none of the patient's biomarkers matched a "
                           f"':{self.b.BIO}' node by LOINC or name — check LOINC coverage and "
                           "name synonyms against the contract's join keys.")

            # Required single-hop patterns present anywhere in the graph?
            for pid, cypher in self._pattern_probes().items():
                optional = self.c.traversal(pid).optional
                if await self._count(cypher) == 0:
                    tag = "MISSING_TRAVERSAL" if not optional else "MISSING_TRAVERSAL(optional)"
                    out.append(f"{tag}: traversal '{pid}' "
                               f"({'|'.join(self.c.rels_for(pid))}) has 0 edges in the graph.")

            # Threshold gate properties present where thresholds are used?
            missing_props = await self._count(
                f"MATCH (:{self.b.BIO})-[:{self.b.rel_bt}]->(t:{self.b.THR}) "
                f"WHERE t.{self.b.op_p} IS NULL OR t.{self.b.val_p} IS NULL "
                f"RETURN count(t) AS c")
            if missing_props > 0:
                out.append(f"MISSING_PROPERTIES: {missing_props} ':{self.b.THR}' node(s) lack "
                           f"'{self.b.op_p}'/'{self.b.val_p}' — the directional gate cannot fire.")

            if not out:
                out.append("GENUINE_ABSENCE: biomarkers matched and the required traversals exist, "
                           "but no disease/finding edge originates from the patient's specific "
                           "biomarker states — no clinical finding for this panel.")
        except Exception as exc:  # noqa: BLE001 — diagnostics must never break reasoning
            logger.warning("Graph diagnostics probe failed: %s", exc)
            out.append(f"DIAGNOSTICS_UNAVAILABLE: could not probe the graph ({exc}).")
        return out

    def _pattern_probes(self) -> Dict[str, str]:
        """One existence-count query per required single-hop traversal pattern."""
        probes: Dict[str, str] = {}
        for pid in ("biomarker_threshold", "threshold_indicates", "biomarker_indicates",
                    "biomarker_associated", "recommendation_outcome", "outcome_followup",
                    "disease_contradiction"):
            if not self.c.has_traversal(pid):
                continue
            tp = self.c.traversal(pid)
            src = self._pred("a", tp.from_labels)
            tgt = self._pred("z", tp.to_labels)
            rel = "|".join(tp.rels)
            probes[pid] = f"MATCH (a)-[:{rel}]->(z) WHERE {src} AND {tgt} RETURN count(*) AS c"
        return probes

    def _pred(self, var: str, labels: List[str]) -> str:
        mapped = [self.c.label(lbl) for lbl in labels]
        return "(" + " OR ".join(f"{var}:{lbl}" for lbl in mapped) + ")"
