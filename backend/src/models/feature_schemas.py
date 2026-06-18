"""
Pydantic schemas — Layer 3 (Feature Generation) request/response contracts.

Typed boundary for the feature-generation API: normalized biomarkers in,
generated clinical features out. Distinct from the internal service dataclass
(``feature_generator.GeneratedFeature``) — this module is the *API contract*.
(Pattern/disease inference happens in Layer 4 over the graph, not here.)
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from models.normalization_schemas import NormalizedBiomarker


class FeatureType(str, Enum):
    """Category of a generated feature."""

    BINARY = "BINARY"
    SEVERITY = "SEVERITY"
    RATIO = "RATIO"
    PATTERN = "PATTERN"


class SeverityLevel(str, Enum):
    """Graded severity band for a biomarker."""

    NORMAL = "normal"
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    CRITICAL = "critical"


class GeneratedFeature(BaseModel):
    """A single clinical feature produced by Layer 3 (API representation)."""

    feature_id: str = Field(..., description="Unique feature key, e.g. 'hemoglobin_low'.")
    feature_name: str = Field(..., description="Human-readable feature name.")
    feature_type: FeatureType = Field(..., description="BINARY / SEVERITY / RATIO / PATTERN.")
    value: Union[bool, str, float] = Field(
        ..., description="bool (BINARY) · severity str (SEVERITY) · float (RATIO/PATTERN)."
    )
    severity: Optional[SeverityLevel] = Field(
        default=None, description="Severity band, when applicable (SEVERITY features)."
    )
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Confidence in the feature in [0, 1]."
    )
    clinical_significance: str = Field(
        default="", description="What abnormality this feature indicates (audit trail)."
    )
    source_biomarkers: List[str] = Field(
        default_factory=list, description="Biomarker codes that contributed to this feature."
    )
    calculation_method: Optional[str] = Field(
        default=None, description="Formula / rule for RATIO or PATTERN features."
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "feature_id": "hemoglobin_low",
                "feature_name": "Hemoglobin Low",
                "feature_type": "BINARY",
                "value": True,
                "severity": None,
                "confidence": 0.96,
                "clinical_significance": "indicates_anemia",
                "source_biomarkers": ["HGB"],
                "calculation_method": None,
            }
        }
    )


class PatternMatch(BaseModel):
    """A detected clinical pattern (API representation)."""

    pattern_id: str = Field(..., description="Pattern key, e.g. 'iron_deficiency_anemia'.")
    pattern_name: str = Field(..., description="Human-readable pattern name.")
    feature_composition: List[str] = Field(
        ..., description="Feature ids that define the pattern."
    )
    matched_features: List[str] = Field(
        default_factory=list, description="Composition features that were present."
    )
    missing_features: List[str] = Field(
        default_factory=list, description="Composition features that were absent."
    )
    match_percentage: float = Field(
        ..., ge=0.0, le=100.0, description="Percentage of required features present (0–100)."
    )
    confidence: float = Field(..., ge=0.0, le=1.0, description="Pattern confidence in [0, 1].")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "pattern_id": "iron_deficiency_anemia",
                "pattern_name": "Iron Deficiency Anemia",
                "feature_composition": ["hemoglobin_low", "mcv_low", "rdw_high"],
                "matched_features": ["hemoglobin_low", "mcv_low", "rdw_high"],
                "missing_features": [],
                "match_percentage": 100.0,
                "confidence": 0.95,
            }
        }
    )


class FeatureGenerationRequest(BaseModel):
    """Incoming request: normalized biomarkers + generation toggles."""

    normalized_biomarkers: List[NormalizedBiomarker] = Field(
        ..., description="Normalized biomarkers from Layer 2."
    )
    include_patterns: bool = Field(default=True, description="Run disease-pattern detection.")
    include_ratios: bool = Field(default=True, description="Compute multi-biomarker ratios.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "normalized_biomarkers": [
                    NormalizedBiomarker.model_config["json_schema_extra"]["example"]
                ],
                "include_patterns": True,
                "include_ratios": True,
            }
        }
    )


class FeatureGenerationResult(BaseModel):
    """Outgoing result: generated features + detected patterns + run metadata."""

    status: str = Field(..., description="Overall outcome: 'success' / 'partial' / 'failed'.")
    generated_features: List[GeneratedFeature] = Field(
        default_factory=list, description="All generated features."
    )
    detected_patterns: List[PatternMatch] = Field(
        default_factory=list, description="Patterns that met the confidence threshold."
    )
    total_features_generated: int = Field(..., ge=0, description="Count of generated features.")
    quality_issues: List[str] = Field(
        default_factory=list, description="Generation warnings (e.g. missing biomarkers)."
    )
    generation_timestamp: str = Field(..., description="ISO-8601 timestamp of the run.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "generated_features": [GeneratedFeature.model_config["json_schema_extra"]["example"]],
                "detected_patterns": [PatternMatch.model_config["json_schema_extra"]["example"]],
                "total_features_generated": 1,
                "quality_issues": [],
                "generation_timestamp": "2026-06-10T00:00:00+00:00",
            }
        }
    )


class Layer3Output(BaseModel):
    """
    Layer 3's complete output — the **facts** handed to Layer 4 (graph reasoning).

    Bundles the normalized biomarkers (Layer 2) the features were computed from
    and the generated features (binary / severity / ratio). Layer 4 infers
    diseases and recommendations from these facts by traversing the Neo4j
    knowledge graph (``Biomarker → Threshold → Disease``).

    ``detected_patterns`` is **legacy and always empty**: Layer 3 no longer
    performs disease/pattern inference, and Layer 4 does not consume this field.
    It is retained (optional, defaulting to ``[]``) only for backward
    compatibility of the contract.
    """

    normalized_biomarkers: List[NormalizedBiomarker] = Field(
        ..., description="Normalized biomarkers (Layer 2) used to compute features."
    )
    generated_features: List[GeneratedFeature] = Field(
        default_factory=list, description="All facts generated in Layer 3 (binary/severity/ratio)."
    )
    detected_patterns: List[PatternMatch] = Field(
        default_factory=list,
        description="LEGACY — always empty; disease inference moved to Layer 4 (Neo4j). "
                    "Not consumed by any downstream service.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "normalized_biomarkers": [
                    NormalizedBiomarker.model_config["json_schema_extra"]["example"]
                ],
                "generated_features": [GeneratedFeature.model_config["json_schema_extra"]["example"]],
                "detected_patterns": [PatternMatch.model_config["json_schema_extra"]["example"]],
            }
        }
    )
