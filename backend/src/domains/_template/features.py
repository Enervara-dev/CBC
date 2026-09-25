"""
<PANEL> Layer-3 feature definitions.

Define the binary / severity / ratio / pattern features for your panel and
collect them into ``FEATURE_REGISTRY`` (keyed by feature id). Import the shared
``FeatureDefinition`` from ``domains.feature_types`` (as ``domains/cbc/features.py``
and ``domains/lft/features.py`` do) — it is the panel-agnostic contract.

Feature ids must be ``<canonical name>_<low|high>`` for binary features, where the
name is the ``CODE_TO_NAME`` value from ``biomarkers.py``: Layer 3 builds ids that
way from each biomarker's status, and a mismatch means the definition never fires.
"""

from __future__ import annotations

from typing import Any, Dict

# feature_id → FeatureDefinition. Empty until you add this panel's features.
FEATURE_REGISTRY: Dict[str, Any] = {}
