"""
<PANEL> Layer-3 feature definitions.

Define the binary / severity / ratio / pattern features for your panel and
collect them into ``FEATURE_REGISTRY`` (keyed by feature id). Use
``domains/cbc/features.py`` as the worked reference — its ``FeatureDefinition``
dataclass and helpers can be imported and reused if your panel fits the same shape.
"""

from __future__ import annotations

from typing import Any, Dict

# feature_id → FeatureDefinition. Empty until you add this panel's features.
FEATURE_REGISTRY: Dict[str, Any] = {}
