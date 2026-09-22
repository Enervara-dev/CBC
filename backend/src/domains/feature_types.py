"""
The Layer-3 feature *contract* — shared by every panel.

``FeatureDefinition`` and its validator are panel-agnostic (a threshold is a
threshold whether the marker is haemoglobin or ALT), so they live here rather
than inside one panel. Each domain's ``features.py`` imports this dataclass and
fills a ``FEATURE_REGISTRY`` with its own definitions.

Feature types
-------------
BINARY   : a single biomarker in/out of range (e.g. ``hemoglobin_low``).
SEVERITY : a single biomarker graded into bands (e.g. ``alt_severity``).
RATIO    : a value computed from several biomarkers (e.g. ``de_ritis_ratio``).
PATTERN  : a composition of other features into a disease pattern
           (e.g. ``cholestatic_pattern``).

BINARY threshold convention
---------------------------
A ``*_low`` feature stores its cutoff in ``threshold_high`` and is TRUE when
``value < threshold_high``. A ``*_high`` feature stores its cutoff in
``threshold_low`` and is TRUE when ``value > threshold_low``. (i.e. the stored
threshold is always the reference bound that the value must cross to be abnormal.)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

VALID_FEATURE_TYPES = ("BINARY", "SEVERITY", "RATIO", "PATTERN")


@dataclass
class FeatureDefinition:
    """
    One clinical feature in a panel's library.

    Attributes
    ----------
    feature_id : str               unique key, e.g. "hemoglobin_low"
    feature_type : str             one of VALID_FEATURE_TYPES
    feature_name : str             human-readable name
    biomarker_id : str             primary biomarker (BINARY/SEVERITY); "" for multi-marker
    threshold_low : Optional[float]   cutoff for "*_high" features (abnormal if value > this)
    threshold_high : Optional[float]  cutoff for "*_low" features (abnormal if value < this)
    unit : Optional[str]           unit the thresholds are expressed in
    severity_levels : Optional[List[str]]   ordered bands (SEVERITY)
    threshold_ranges : Optional[Dict[str, Tuple[float, float]]]
                                   band -> (min, max) interval (SEVERITY)
    feature_composition : Optional[List[str]]   referenced feature_ids (PATTERN)
    calculation_method : Optional[str]   formula / rule (RATIO, PATTERN)
    clinical_significance : str    what abnormality this indicates (audit trail)
    description : str              plain-language description (audit trail)
    """

    feature_id: str
    feature_type: str
    feature_name: str = ""
    biomarker_id: str = ""
    threshold_low: Optional[float] = None
    threshold_high: Optional[float] = None
    unit: Optional[str] = None
    severity_levels: Optional[List[str]] = None
    threshold_ranges: Optional[Dict[str, Tuple[float, float]]] = None
    feature_composition: Optional[List[str]] = None
    calculation_method: Optional[str] = None
    clinical_significance: str = ""
    description: str = ""

    def as_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict (audit / logging / JSON)."""
        return asdict(self)


def validate_feature_definition(fd: FeatureDefinition) -> bool:
    """
    Validate a feature definition's internal consistency.

    Checks, by type:
      * feature_id present; feature_type is one of VALID_FEATURE_TYPES
      * BINARY   — at least one of threshold_low / threshold_high is set
      * SEVERITY — severity_levels + threshold_ranges set; each band is named in
                   severity_levels and is a 2-tuple with min <= max
      * RATIO    — calculation_method set
      * PATTERN  — feature_composition (non-empty) or calculation_method set

    Returns
    -------
    bool
        True if the definition is well-formed.
    """
    if not fd.feature_id or not isinstance(fd.feature_id, str):
        return False
    if fd.feature_type not in VALID_FEATURE_TYPES:
        return False

    if fd.feature_type == "BINARY":
        return fd.threshold_low is not None or fd.threshold_high is not None

    if fd.feature_type == "SEVERITY":
        if not fd.severity_levels or not fd.threshold_ranges:
            return False
        for band, rng in fd.threshold_ranges.items():
            if band not in fd.severity_levels:
                return False
            if not (isinstance(rng, (tuple, list)) and len(rng) == 2):
                return False
            low, high = rng
            if low is None or high is None or low > high:
                return False
        return True

    if fd.feature_type == "RATIO":
        return bool(fd.calculation_method)

    if fd.feature_type == "PATTERN":
        return bool(fd.feature_composition) or bool(fd.calculation_method)

    return False


__all__ = ["FeatureDefinition", "VALID_FEATURE_TYPES", "validate_feature_definition"]
