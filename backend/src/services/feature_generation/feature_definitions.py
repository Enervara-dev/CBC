"""
Backwards-compatible shim.

CBC feature definitions now live in ``domains.cbc.features`` (the per-panel
knowledge folder). This module re-exports them so existing imports keep working.
New code should import from ``domains.cbc.features`` (or via the domain registry).

Note: these are **CBC only**. The running pipeline is multi-panel and builds its
Layer-3 / Layer-5 knowledge from ``domains.registry.merged_feature_registry()``
and ``merged_validation_rules()``, which cover CBC, LFT, Lipid, and any panel
registered later.
"""

from domains.cbc.features import (  # noqa: F401
    FeatureDefinition,
    VALID_FEATURE_TYPES,
    BINARY_FEATURES,
    SEVERITY_FEATURES,
    COMPUTED_RATIO_FEATURES,
    PATTERN_FEATURES,
    ANEMIA_SUBTYPE_PATTERNS,
    FEATURE_REGISTRY,
    get_feature,
    list_all_features,
    get_features_by_type,
    get_patterns,
    validate_feature_definition,
)

__all__ = [
    "FeatureDefinition",
    "VALID_FEATURE_TYPES",
    "BINARY_FEATURES",
    "SEVERITY_FEATURES",
    "COMPUTED_RATIO_FEATURES",
    "PATTERN_FEATURES",
    "ANEMIA_SUBTYPE_PATTERNS",
    "FEATURE_REGISTRY",
    "get_feature",
    "list_all_features",
    "get_features_by_type",
    "get_patterns",
    "validate_feature_definition",
]
