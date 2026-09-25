"""
Layer 4 — clinical reasoning.

``RuleBasedClinicalReasoner`` turns Layer-3 facts into explained findings using
the domain interpretation tables, with no knowledge graph required.
``CompositeReasoner`` runs it always and merges graph findings on top when a
graph is reachable. Both emit the same ``Layer4Output`` as
``services.graph_reasoning.GraphReasoningEngine``, so Layers 5 and 6 are
indifferent to which produced a result.
"""

from services.clinical_reasoning.composite import CompositeReasoner
from services.clinical_reasoning.rule_reasoner import RuleBasedClinicalReasoner

__all__ = ["RuleBasedClinicalReasoner", "CompositeReasoner"]
