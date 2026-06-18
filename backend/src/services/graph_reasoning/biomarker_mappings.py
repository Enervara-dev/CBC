"""
Biomarker / fact ↔ knowledge-graph bridging for Layer 4.

Layer 3 emits facts keyed by feature id (``hemoglobin_low``) and Layer 2 keys
biomarkers by canonical code (``HGB``). The Neo4j graph keys ``Biomarker`` nodes by
name (``Hemoglobin``) / id (``biomarker::hemoglobin``). This module maps between
them so the reasoning engine can traverse ``Biomarker → Threshold → Disease``.
"""

from __future__ import annotations

from typing import List, Optional, Union

# Fact↔biomarker and code↔graph-name tables live in the domain folder (single
# source of truth shared with the rest of the pipeline). The live graph mixes
# American node names ("Hemoglobin") with British spelling and synonym nodes
# (e.g. "RCDW"); CODE_TO_GRAPH_NAMES lists every known form for exact-name
# matching to avoid substring collisions (e.g. "mch" vs "mchc").
from domains.cbc.biomarkers import FACT_TO_BIOMARKER, CODE_TO_GRAPH_NAMES


class BiomarkerFactMapping:
    """Map Layer 3 facts ↔ canonical biomarker codes ↔ graph biomarker names."""

    FACT_TO_BIOMARKER = FACT_TO_BIOMARKER
    CODE_TO_GRAPH_NAMES = CODE_TO_GRAPH_NAMES

    def get_biomarker_for_fact(self, fact_id: str) -> Optional[Union[str, List[str]]]:
        """Return the biomarker code (or list of codes) a fact maps to, or None."""
        return self.FACT_TO_BIOMARKER.get(fact_id)

    def graph_names_for_code(self, code: str) -> List[str]:
        """Lowercased graph Biomarker.name candidates for a canonical code."""
        return self.CODE_TO_GRAPH_NAMES.get(code, [code.lower()])

    def graph_ids_for_code(self, code: str) -> List[str]:
        """Candidate ``biomarker::<name>`` ids for a canonical code."""
        return [f"biomarker::{n}" for n in self.graph_names_for_code(code)]

    @staticmethod
    def direction_for_fact(fact_id: str) -> Optional[str]:
        """'low'/'high' from a binary fact's suffix; None for non-directional facts."""
        if fact_id.endswith("_low"):
            return "low"
        if fact_id.endswith("_high"):
            return "high"
        return None
