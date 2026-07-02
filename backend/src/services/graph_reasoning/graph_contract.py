"""
Graph Contract loader (Layer 4).

The Graph Contract (``graph_contract.yaml``) is the **single source of truth** for
the clinical knowledge graph's labels, relationship types, traversal patterns,
properties, constraints and indexes. Both the ingestion pipeline and this
reasoning engine read the same contract — nothing about the graph schema is
hard-coded independently in the reasoning code.

This module handles **contract loading only** (requirement 5 keeps loading,
traversal generation, and reasoning logic separate):

    load_contract()  →  GraphContract   (immutable, queryable view of the YAML)

Resolution order for the contract file:
    1. explicit ``path`` argument
    2. ``GRAPH_CONTRACT_PATH`` environment variable (point this at the shared
       file published by the ingestion pipeline)
    3. the bundled ``graph_contract.yaml`` next to this module
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_BUNDLED_CONTRACT = Path(__file__).resolve().parent / "graph_contract.yaml"


class GraphContractError(RuntimeError):
    """The contract file is missing, unreadable, or structurally invalid."""


@dataclass(frozen=True)
class TraversalPattern:
    """One single-hop traversal the reasoning engine is allowed to perform."""

    id: str
    from_labels: List[str]
    rels: List[str]
    to_labels: List[str]
    optional: bool = False


@dataclass(frozen=True)
class GraphContract:
    """An immutable, queryable view of ``graph_contract.yaml``."""

    version: str
    umbrella_label: str
    common_node_properties: Dict[str, List[str]]
    common_edge_properties: Dict[str, List[str]]
    node_labels: Dict[str, Dict[str, Any]]          # label name → spec
    relationship_types: Dict[str, Dict[str, Any]]   # rel name → spec
    traversal_patterns: Dict[str, TraversalPattern]  # id → pattern
    constraints: List[Dict[str, Any]]
    indexes: List[Dict[str, Any]]
    validation: Dict[str, Any]
    source_path: str
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    # ── Version ──────────────────────────────────────────────────────────────
    @property
    def major_version(self) -> int:
        """Major semver component (consumers assert the major they support)."""
        try:
            return int(str(self.version).split(".", 1)[0])
        except (ValueError, IndexError):  # pragma: no cover - malformed version
            raise GraphContractError(f"Unparseable contract_version {self.version!r}")

    # ── Labels ───────────────────────────────────────────────────────────────
    def label(self, name: str) -> str:
        """Neo4j label for a contract node type (``neo4j_label`` overrides name)."""
        spec = self.node_labels.get(name)
        if spec is None:
            raise GraphContractError(f"Contract has no node label {name!r}.")
        return str(spec.get("neo4j_label", name))

    def has_label(self, name: str) -> bool:
        return name in self.node_labels

    def has_relationship(self, name: str) -> bool:
        return name in self.relationship_types

    def node_property(self, label: str, prop: str) -> str:
        """
        Return ``prop`` if the contract declares it for ``label`` (as a common,
        required or recommended property); raise otherwise. This ties every
        property name the engine reads to the contract instead of a literal.
        """
        declared = set(self.common_node_properties.get("required", []))
        declared |= set(self.common_node_properties.get("recommended", []))
        spec = self.node_labels.get(label, {})
        declared |= set(spec.get("required", []) or [])
        declared |= set(spec.get("recommended", []) or [])
        if prop not in declared:
            raise GraphContractError(
                f"Contract does not declare property {prop!r} on {label!r} "
                f"(declared: {sorted(declared)})."
            )
        return prop

    # ── Constraints / indexes ────────────────────────────────────────────────
    def id_property(self) -> str:
        """The umbrella-label unique-id property (from ``constraints``)."""
        for c in self.constraints:
            if c.get("label") == self.umbrella_label and c.get("type") == "unique":
                return str(c["property"])
        return self.node_property(self.umbrella_label if self.has_label(self.umbrella_label)
                                  else next(iter(self.node_labels)), "id")

    def index_property(self, index_name: str) -> Optional[str]:
        """Property backing a named index (e.g. ``biomarker_loinc`` → loinc_code)."""
        for ix in self.indexes:
            if ix.get("name") == index_name:
                return str(ix["property"])
        return None

    # ── Traversals ───────────────────────────────────────────────────────────
    def traversal(self, pattern_id: str) -> TraversalPattern:
        try:
            return self.traversal_patterns[pattern_id]
        except KeyError:
            raise GraphContractError(
                f"Contract has no traversal pattern {pattern_id!r} "
                f"(available: {sorted(self.traversal_patterns)})."
            )

    def has_traversal(self, pattern_id: str) -> bool:
        return pattern_id in self.traversal_patterns

    def rels_for(self, pattern_id: str) -> List[str]:
        """Relationship type(s) a traversal pattern uses (derived, never hard-coded)."""
        return list(self.traversal(pattern_id).rels)


# ── Loading ──────────────────────────────────────────────────────────────────
def _resolve_path(path: Optional[str]) -> Path:
    if path:
        return Path(path)
    env = os.getenv("GRAPH_CONTRACT_PATH")
    if env:
        return Path(env)
    return _BUNDLED_CONTRACT


def _as_traversals(items: Any) -> Dict[str, TraversalPattern]:
    out: Dict[str, TraversalPattern] = {}
    for item in items or []:
        pid = item.get("id")
        if not pid:
            raise GraphContractError("A traversal_pattern is missing its 'id'.")
        out[pid] = TraversalPattern(
            id=pid,
            from_labels=list(item.get("from", []) or []),
            rels=list(item.get("rels", []) or []),
            to_labels=list(item.get("to", []) or []),
            optional=bool(item.get("optional", False)),
        )
    return out


def load_contract(path: Optional[str] = None) -> GraphContract:
    """Load and parse the Graph Contract into a :class:`GraphContract`."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - env-specific
        raise GraphContractError(
            "PyYAML is required to read the Graph Contract. Run: pip install pyyaml"
        ) from exc

    contract_path = _resolve_path(path)
    if not contract_path.is_file():
        raise GraphContractError(f"Graph Contract not found at {contract_path}.")
    try:
        raw = yaml.safe_load(contract_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise GraphContractError(f"Failed to parse {contract_path}: {exc}") from exc

    version = raw.get("contract_version")
    if not version:
        raise GraphContractError("Contract is missing 'contract_version'.")

    node_labels = {n["name"]: n for n in raw.get("node_labels", []) if n.get("name")}
    rel_types = {r["name"]: r for r in raw.get("relationship_types", []) if r.get("name")}

    return GraphContract(
        version=str(version),
        umbrella_label=str((raw.get("neo4j") or {}).get("umbrella_label", "Entity")),
        common_node_properties=dict(raw.get("common_node_properties", {}) or {}),
        common_edge_properties=dict(raw.get("common_edge_properties", {}) or {}),
        node_labels=node_labels,
        relationship_types=rel_types,
        traversal_patterns=_as_traversals(raw.get("traversal_patterns")),
        constraints=list(raw.get("constraints", []) or []),
        indexes=list(raw.get("indexes", []) or []),
        validation=dict(raw.get("validation", {}) or {}),
        source_path=str(contract_path),
        raw=raw,
    )
