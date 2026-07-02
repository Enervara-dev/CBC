"""
Pydantic schemas — Layer 4 (Graph Reasoning) input/output contracts.

Layer 4 validates the Layer 3 disease patterns against the Neo4j clinical
knowledge graph, builds human-readable evidence chains, detects contradictory
findings, and produces prioritised recommendations — all with a full audit trail.
"""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field


class EvidenceLink(BaseModel):
    """One biomarker's contribution to a finding, with a readable narrative."""

    biomarker_id: str = Field(..., description="Canonical biomarker code, e.g. 'HGB'.")
    biomarker_name: str = Field(..., description="Human-readable biomarker name.")
    evidence_strength: float = Field(
        ..., ge=0.0, le=1.0, description="Strength of this biomarker→finding edge in [0, 1]."
    )
    narrative: str = Field(
        ..., description='Readable statement, e.g. "HGB=10.2 (LOW) supports Anemia (strength: 0.95)".'
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "biomarker_id": "HGB",
                "biomarker_name": "Hemoglobin",
                "evidence_strength": 0.95,
                "narrative": "HGB=10.2 (LOW) supports Iron Deficiency Anemia (strength: 0.95)",
            }
        }
    )


class ValidatedFinding(BaseModel):
    """A Layer 3 pattern validated against the graph, with its evidence chain."""

    finding_id: str = Field(..., description="Finding key, e.g. 'iron_deficiency_anemia'.")
    finding_name: str = Field(..., description="Human-readable finding name.")
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence carried from the Layer 3 pattern match."
    )
    severity: str | None = Field(
        default=None, description="'mild' / 'moderate' / 'severe' / 'critical', if known."
    )
    evidence_chain: List[EvidenceLink] = Field(
        default_factory=list, description="Biomarker evidence supporting this finding."
    )
    source_pattern: str = Field(..., description="The Layer 3 pattern id that produced this finding.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "finding_id": "iron_deficiency_anemia",
                "finding_name": "Iron Deficiency Anemia",
                "confidence": 0.95,
                "severity": "moderate",
                "evidence_chain": [EvidenceLink.model_config["json_schema_extra"]["example"]],
                "source_pattern": "iron_deficiency_anemia",
            }
        }
    )


class Recommendation(BaseModel):
    """A clinical recommendation derived from a validated finding."""

    recommendation_id: str = Field(..., description="Recommendation key, e.g. 'order_iron_studies'.")
    recommendation_name: str = Field(..., description="Human-readable recommendation.")
    recommendation_type: str = Field(..., description="TEST / REFERRAL / MONITOR / ACTION.")
    priority: int = Field(..., ge=1, le=10, description="1–10; lower = higher priority.")
    urgency: str = Field(..., description="routine / urgent / stat.")
    from_finding: str = Field(..., description="Finding id that recommended this.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "recommendation_id": "order_iron_studies",
                "recommendation_name": "Order iron studies (ferritin, TIBC)",
                "recommendation_type": "TEST",
                "priority": 2,
                "urgency": "urgent",
                "from_finding": "iron_deficiency_anemia",
            }
        }
    )


class ConflictAlert(BaseModel):
    """Two findings that contradict each other, with a handling recommendation."""

    finding1_id: str = Field(..., description="First finding id.")
    finding1_name: str = Field(..., description="First finding name.")
    finding2_id: str = Field(..., description="Second finding id.")
    finding2_name: str = Field(..., description="Second finding name.")
    conflict_severity: str = Field(..., description="low / medium / high.")
    recommendation: str = Field(
        ..., description="How to handle, e.g. 'MANUAL_REVIEW', 'PREFER_FINDING_1'."
    )
    note: str = Field(..., description="Explanation of the conflict.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "finding1_id": "iron_deficiency_anemia",
                "finding1_name": "Iron Deficiency Anemia",
                "finding2_id": "anemia_of_chronic_disease",
                "finding2_name": "Anemia of Chronic Disease",
                "conflict_severity": "high",
                "recommendation": "MANUAL_REVIEW",
                "note": "Both anemia subtypes matched; iron studies needed to disambiguate.",
            }
        }
    )


class AuditTrail(BaseModel):
    """Full traceability for one Layer 4 reasoning run."""

    timestamp: str = Field(..., description="ISO-8601 start timestamp of the run.")
    layer3_input: Dict[str, Any] = Field(..., description="The Layer 3 output that was reasoned over.")
    neo4j_queries_executed: List[str] = Field(
        default_factory=list, description="Names of the Cypher queries that ran."
    )
    nodes_queried: int = Field(..., ge=0, description="Total graph results consumed across queries.")
    patterns_validated: int = Field(..., ge=0, description="Patterns submitted for validation.")
    findings_extracted: int = Field(..., ge=0, description="Validated findings produced.")
    conflicts_detected: int = Field(..., ge=0, description="Conflicts detected.")
    recommendations_generated: int = Field(..., ge=0, description="Recommendations after dedup.")
    execution_time_ms: float = Field(..., ge=0.0, description="Wall-clock duration in milliseconds.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "timestamp": "2026-06-11T00:00:00+00:00",
                "layer3_input": {"detected_patterns": 2, "normalized_biomarkers": 6},
                "neo4j_queries_executed": [
                    "validate_pattern_in_graph", "get_evidence_chain",
                    "detect_conflicts", "get_recommendations",
                ],
                "nodes_queried": 14,
                "patterns_validated": 2,
                "findings_extracted": 2,
                "conflicts_detected": 1,
                "recommendations_generated": 3,
                "execution_time_ms": 42.7,
            }
        }
    )


class Layer4Output(BaseModel):
    """The complete Layer 4 result."""

    validated_findings: List[ValidatedFinding] = Field(
        default_factory=list, description="Graph-validated findings with evidence."
    )
    recommendations: List[Recommendation] = Field(
        default_factory=list, description="Deduplicated, priority-sorted recommendations."
    )
    conflicts: List[ConflictAlert] = Field(
        default_factory=list, description="Detected contradictory findings."
    )
    audit_trail: AuditTrail = Field(..., description="Traceability for the run.")
    status: str = Field(..., description="'success' / 'partial' / 'failed'.")
    error_messages: List[str] = Field(
        default_factory=list, description="Per-step failures (status is 'partial' if non-empty)."
    )
    diagnostics: List[str] = Field(
        default_factory=list,
        description="Explicit zero-result diagnostics (empty graph / contract violation / "
                    "missing traversal / unmatched biomarkers / genuine absence). Empty on success.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "validated_findings": [ValidatedFinding.model_config["json_schema_extra"]["example"]],
                "recommendations": [Recommendation.model_config["json_schema_extra"]["example"]],
                "conflicts": [ConflictAlert.model_config["json_schema_extra"]["example"]],
                "audit_trail": AuditTrail.model_config["json_schema_extra"]["example"],
                "status": "success",
                "error_messages": [],
            }
        }
    )
