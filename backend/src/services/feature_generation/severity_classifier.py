"""
Severity classifier — map a biomarker value to a severity band.

Severity bands and their numeric ranges come from ``SEVERITY_FEATURES`` in
``feature_definitions.py`` (the single source of truth), so clinical thresholds
live in one auditable place. The classifier is static / stateless.

Example (Hemoglobin, g/dL):
    critical : 0.0 – 3.0
    severe   : 3.0 – 7.0
    moderate : 7.0 – 10.0
    mild     : 10.0 – 12.0
    normal   : 12.0+
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Tuple

from services.feature_generation.feature_definitions import SEVERITY_FEATURES

if TYPE_CHECKING:  # type-only; avoids importing the sqlalchemy-backed normalizer
    from services.normalization.normalizer import NormalizedBiomarker


class SeverityClassifier:
    """
    Static severity classification driven by the SEVERITY_FEATURES definitions.

    A severity feature is keyed by its biomarker (HGB/WBC/PLT) and carries a
    ``threshold_ranges`` dict of ``band -> (low, high)``. Classification finds the
    band whose half-open interval ``[low, high)`` contains the value (clamped at
    the extremes).
    """

    # biomarker_id -> band -> (low, high)
    _THRESHOLDS_BY_BIOMARKER: Dict[str, Dict[str, Tuple[float, float]]] = {
        fd.biomarker_id: fd.threshold_ranges
        for fd in SEVERITY_FEATURES.values()
        if fd.biomarker_id and fd.threshold_ranges
    }

    @staticmethod
    def classify_severity(biomarker_id: str, value: float) -> str:
        """
        Return the severity level for ``value`` of ``biomarker_id``.

        Returns one of ``"normal" | "mild" | "moderate" | "severe" | "critical"``.
        If the biomarker has no severity definition, returns ``"normal"`` (it has
        no graded severity, so it is treated as unremarkable).
        """
        thresholds = SeverityClassifier._get_severity_thresholds(biomarker_id)
        if not thresholds:
            return "normal"
        return SeverityClassifier._find_severity_level(value, thresholds)

    @staticmethod
    def classify_all(normalized_biomarkers: List["NormalizedBiomarker"]) -> Dict[str, str]:
        """
        Classify every biomarker that has a severity definition.

        Returns
        -------
        Dict[str, str]
            ``{biomarker_id: severity_level}`` (only biomarkers with severity
            ranges are included), e.g. ``{"HGB": "moderate", "PLT": "severe"}``.
        """
        result: Dict[str, str] = {}
        for biomarker in normalized_biomarkers:
            code = biomarker.biomarker_id
            if code in SeverityClassifier._THRESHOLDS_BY_BIOMARKER:
                result[code] = SeverityClassifier.classify_severity(code, biomarker.value)
        return result

    # ── Helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _get_severity_thresholds(biomarker_id: str) -> Dict[str, Tuple[float, float]]:
        """Return the ``band -> (low, high)`` ranges for a biomarker (or ``{}``)."""
        return SeverityClassifier._THRESHOLDS_BY_BIOMARKER.get(
            biomarker_id.strip().upper(), {}
        )

    @staticmethod
    def _find_severity_level(value: float, thresholds: Dict[str, Tuple[float, float]]) -> str:
        """
        Find the band whose ``[low, high)`` interval contains ``value``.

        Values below the lowest band clamp to it; values at/above the highest band
        clamp to it (so a value above the 'normal' ceiling stays 'normal').
        """
        for band, (low, high) in thresholds.items():
            if low <= value < high:
                return band
        ordered = sorted(thresholds.items(), key=lambda kv: kv[1][0])
        if value < ordered[0][1][0]:
            return ordered[0][0]
        return ordered[-1][0]
