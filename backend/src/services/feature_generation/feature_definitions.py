"""
Backwards-compatible shim.

CBC feature definitions now live in ``domains.cbc.features`` (the per-panel
knowledge folder). This module re-exports them so existing imports keep working.
New code should import from ``domains.cbc.features`` (or via the domain registry).
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
