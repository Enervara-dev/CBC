"""
Contract compatibility checks (Layer 4).

Kept separate from loading and from the reasoning logic (requirement 5) so the
engine can support future contract versions by adjusting only this module.

``check_compatibility`` runs **before reasoning begins** and fails fast with a
clear :class:`ContractCompatibilityError` instead of silently returning zero
findings when the contract is incompatible.
"""

from __future__ import annotations

from typing import List

from services.graph_reasoning.graph_contract import GraphContract

# The contract major version this reasoning engine was written against.
SUPPORTED_MAJOR_VERSION = 1

# Traversal patterns the reasoning algorithms depend on. Absence of any of these
# (unless the pattern itself is declared optional) makes reasoning meaningless.
REQUIRED_TRAVERSALS: List[str] = [
    "biomarker_threshold",
    "threshold_indicates",
    "biomarker_indicates",
    "biomarker_associated",
    "recommendation_outcome",
    "outcome_followup",
]

# Node labels the engine reads by name in RETURN/predicate clauses.
REQUIRED_LABELS: List[str] = ["Biomarker", "Threshold", "Disease", "Finding",
                              "Recommendation", "FollowUpTest"]

# Threshold properties the directional gate reads (their *names* come from the
# contract; here we only assert the roles are declared for the Threshold label).
REQUIRED_THRESHOLD_PROPERTIES: List[str] = ["operator", "value", "unit"]


class ContractCompatibilityError(RuntimeError):
    """The loaded Graph Contract is incompatible with this reasoning engine."""


def check_compatibility(contract: GraphContract) -> List[str]:
    """
    Assert the contract is usable by this engine; return non-fatal warnings.

    Raises
    ------
    ContractCompatibilityError
        On a hard incompatibility (wrong major version, a missing required
        traversal, or a required traversal referencing an undeclared
        relationship/label). The message names exactly what is wrong.
    """
    problems: List[str] = []
    warnings: List[str] = []

    # 1. Version — assert the major we were written against.
    if contract.major_version != SUPPORTED_MAJOR_VERSION:
        problems.append(
            f"contract major version {contract.major_version} "
            f"(v{contract.version}) != supported major {SUPPORTED_MAJOR_VERSION}"
        )

    # 2. Required traversal patterns must exist.
    for pid in REQUIRED_TRAVERSALS:
        if not contract.has_traversal(pid):
            problems.append(f"required traversal pattern {pid!r} is absent")

    # 3. Required capabilities: every rel/label a required traversal uses must be
    #    declared in the contract's relationship_types / node_labels.
    for pid in REQUIRED_TRAVERSALS:
        if not contract.has_traversal(pid):
            continue
        tp = contract.traversal(pid)
        for rel in tp.rels:
            if not contract.has_relationship(rel):
                problems.append(f"traversal {pid!r} uses undeclared relationship {rel!r}")
        for lbl in (*tp.from_labels, *tp.to_labels):
            if not contract.has_label(lbl):
                problems.append(f"traversal {pid!r} references undeclared label {lbl!r}")

    # 4. Labels the engine references directly.
    for lbl in REQUIRED_LABELS:
        if not contract.has_label(lbl):
            problems.append(f"required node label {lbl!r} is absent")

    # 5. Threshold gate properties must be declared (SOFT — a warning, because a
    #    graph could carry them without listing them; the query still emits them).
    for prop in REQUIRED_THRESHOLD_PROPERTIES:
        try:
            contract.node_property("Threshold", prop)
        except Exception:
            warnings.append(f"Threshold property {prop!r} not declared in contract (soft)")

    # 6. Optional-but-recommended traversal.
    if not contract.has_traversal("disease_contradiction"):
        warnings.append("optional traversal 'disease_contradiction' absent — no conflict detection")

    if problems:
        raise ContractCompatibilityError(
            "Graph Contract at "
            f"{contract.source_path} is incompatible with this reasoning engine:\n  - "
            + "\n  - ".join(problems)
        )
    return warnings
