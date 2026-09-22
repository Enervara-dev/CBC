"""
Composite Layer-4 reasoner — rules always, graph when it is available.

The knowledge graph is the richer source when it is populated, but it is also
the fragile one: it needs a reachable Neo4j and a subgraph for the panel in
question, and today it covers CBC only. Making it the sole Layer 4 meant that a
missing graph produced *no findings at all* — an LFT with a bilirubin of 15
returned nothing to the clinician.

So the rule reasoner runs first and always. If a graph reasoner is configured and
succeeds, its findings are merged on top: anything it adds that the rules did not
already produce is appended, and its recommendations are merged and re-deduped.
A graph failure is recorded as a diagnostic, not an error — the run still carries
the rule-derived findings.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from models.feature_schemas import Layer3Output
from models.graph_schemas import Layer4Output, Recommendation, ValidatedFinding
from services.clinical_reasoning.rule_reasoner import RuleBasedClinicalReasoner

logger = logging.getLogger(__name__)


class CompositeReasoner:
    """
    Run the rule reasoner, then merge in graph findings when the graph answers.

    Parameters
    ----------
    rule_reasoner :
        The always-on deterministic engine. Built if not supplied.
    graph_reasoner :
        Optional graph engine. ``None`` (or any failure from it) leaves the
        rule-derived result untouched.
    """

    def __init__(
        self,
        rule_reasoner: Optional[RuleBasedClinicalReasoner] = None,
        graph_reasoner: Any = None,
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        self.rules = rule_reasoner or RuleBasedClinicalReasoner()
        self.graph = graph_reasoner
        self.logger = logger_ or logger

    async def reason(self, layer3_output: Layer3Output) -> Layer4Output:
        """Produce the merged Layer-4 result."""
        result = await self.rules.reason(layer3_output)
        if self.graph is None:
            return result

        try:
            graph_result = await self.graph.reason(layer3_output)
        except Exception as exc:                      # graph down, empty, or slow
            self.logger.warning("Graph reasoning unavailable (%s); using rules only", exc)
            result.diagnostics.append(f"Graph reasoning unavailable: {exc}")
            return result

        added = self._merge_findings(result.validated_findings, graph_result.validated_findings)
        if added:
            self.logger.info("Graph contributed %d finding(s) beyond the rules", len(added))
            result.validated_findings.extend(added)
        result.recommendations = self._merge_recommendations(
            result.recommendations, graph_result.recommendations
        )
        result.conflicts.extend(graph_result.conflicts)
        result.audit_trail.neo4j_queries_executed = graph_result.audit_trail.neo4j_queries_executed
        result.audit_trail.nodes_queried = graph_result.audit_trail.nodes_queried
        result.audit_trail.findings_extracted = len(result.validated_findings)
        result.audit_trail.recommendations_generated = len(result.recommendations)
        if graph_result.error_messages:
            result.error_messages.extend(graph_result.error_messages)
            result.status = "partial"
        return result

    @staticmethod
    def _merge_findings(
        existing: List[ValidatedFinding], incoming: List[ValidatedFinding]
    ) -> List[ValidatedFinding]:
        """Return graph findings the rules did not already produce."""
        known_ids = {f.finding_id for f in existing}
        known_names = {f.finding_name.strip().lower() for f in existing}
        return [
            f for f in incoming
            if f.finding_id not in known_ids and f.finding_name.strip().lower() not in known_names
        ]

    @staticmethod
    def _merge_recommendations(
        existing: List[Recommendation], incoming: List[Recommendation]
    ) -> List[Recommendation]:
        """Union both sets, keeping the most urgent instance of each."""
        rank = {"stat": 0, "urgent": 1, "routine": 2}
        best = {r.recommendation_id: r for r in existing}
        for rec in incoming:
            current = best.get(rec.recommendation_id)
            if current is None or rank.get(rec.urgency, 3) < rank.get(current.urgency, 3):
                best[rec.recommendation_id] = rec
        return sorted(best.values(), key=lambda r: (rank.get(r.urgency, 3), r.priority))


__all__ = ["CompositeReasoner"]
