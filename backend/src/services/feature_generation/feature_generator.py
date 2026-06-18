"""
Feature generator — convert Layer 2 normalized biomarkers into clinical *facts*.

Pipeline (per call to :meth:`FeatureGenerator.generate_features`):

    normalized biomarkers
        ├─ binary   : in/out-of-range flags from each biomarker's status
        ├─ severity : graded bands for HGB / WBC / PLT
        └─ ratio    : computed multi-biomarker ratios (skipped if operands missing)
    → List[GeneratedFeature]   (facts only)

Layer 3 emits **facts only** — no disease inference, no pattern matching, no
recommendations. Disease/recommendation inference is performed downstream in
Layer 4 by traversing the Neo4j knowledge graph
(``Biomarker → Threshold → Disease``), so adding a new disease is graph data, not
code. As a result ``Layer3Output.detected_patterns`` is always empty (see
``models.feature_schemas.Layer3Output``).

Missing biomarkers never abort the run: they are logged, tracked, and the
available features are still returned (partial results).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from services.feature_generation.feature_definitions import FeatureDefinition
from services.feature_generation.severity_classifier import SeverityClassifier

if TYPE_CHECKING:  # type-only import (keeps sqlalchemy out of this module)
    from services.normalization.normalizer import NormalizedBiomarker

logger = logging.getLogger(__name__)


# Canonical biomarker code → the base name used in feature ids / ratio formulas.
CODE_TO_BASE: Dict[str, str] = {
    "HGB": "hemoglobin", "HCT": "hematocrit", "RBC": "rbc", "WBC": "wbc",
    "PLT": "platelets", "MCV": "mcv", "MCH": "mch", "MCHC": "mchc",
    "RDW": "rdw", "NEUT": "neutrophils", "LYMPH": "lymphocytes",
}

_STATUS_SUFFIX = {"LOW": "low", "HIGH": "high", "NORMAL": "normal"}


@dataclass
class GeneratedFeature:
    """A single clinical feature produced for a patient."""

    feature_id: str
    feature_type: str                       # BINARY / SEVERITY / RATIO / PATTERN
    value: Any                              # bool (binary) | str (severity) | float (ratio/pattern)
    biomarker_id: Optional[str] = None
    clinical_significance: str = ""
    description: str = ""
    detail: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict."""
        return asdict(self)


class FeatureGenerator:
    """
    Generate clinical features from normalized biomarkers.

    Parameters
    ----------
    feature_definitions : Dict[str, FeatureDefinition]
        The full feature library (e.g. ``FEATURE_REGISTRY`` from
        ``feature_definitions.py``). Partitioned internally by feature type.
    """

    def __init__(self, feature_definitions: Dict[str, FeatureDefinition]) -> None:
        self.feature_definitions = feature_definitions
        self.binary_features = self._by_type("BINARY")
        self.severity_features = self._by_type("SEVERITY")
        self.ratio_features = self._by_type("RATIO")
        # PATTERN definitions are intentionally NOT loaded here: disease/pattern
        # inference moved to Layer 4 (Neo4j knowledge-graph traversal). They remain
        # in the registry only as legacy reference data.

        # Biomarkers a definition expects to see (for missing-data tracking).
        self._expected_codes = {
            fd.biomarker_id for fd in self.feature_definitions.values() if fd.biomarker_id
        }
        self.available_biomarkers: List[str] = []
        self.missing_biomarkers: List[str] = []

    def _by_type(self, feature_type: str) -> Dict[str, FeatureDefinition]:
        return {
            fid: fd for fid, fd in self.feature_definitions.items()
            if fd.feature_type == feature_type
        }

    # ── Public API ───────────────────────────────────────────────────────────
    async def generate_features(
        self, normalized_biomarkers: List["NormalizedBiomarker"]
    ) -> List[GeneratedFeature]:
        """
        Generate all clinical *facts* for one patient's normalized biomarkers.

        Returns binary + severity + ratio features only — **no patterns / disease
        inference** (that is Layer 4's job). Partial results are returned if some
        biomarkers are missing. Tracks ``available_biomarkers`` and
        ``missing_biomarkers`` on the instance.
        """
        present_codes = {b.biomarker_id for b in normalized_biomarkers}
        self.available_biomarkers = sorted(present_codes)
        self.missing_biomarkers = sorted(self._expected_codes - present_codes)
        if self.missing_biomarkers:
            logger.info("Feature generation: missing biomarkers %s", self.missing_biomarkers)

        binary = await self._generate_binary_features(normalized_biomarkers)
        severity = await self._generate_severity_features(normalized_biomarkers)
        ratios = await self._compute_ratio_features(normalized_biomarkers)

        logger.info(
            "Feature generation (facts only): %d binary, %d severity, %d ratio",
            len(binary), len(severity), len(ratios),
        )
        return binary + severity + ratios

    # ── Binary ───────────────────────────────────────────────────────────────
    async def _generate_binary_features(
        self, biomarkers: List["NormalizedBiomarker"]
    ) -> List[GeneratedFeature]:
        """One binary feature per biomarker, keyed by its LOW/HIGH/NORMAL status."""
        features: List[GeneratedFeature] = []
        for b in biomarkers:
            base = CODE_TO_BASE.get(b.biomarker_id)
            if base is None:
                logger.debug("No binary mapping for biomarker %s — skipping", b.biomarker_id)
                continue
            suffix = _STATUS_SUFFIX.get((b.status or "").upper())
            if suffix is None:
                logger.debug("Unknown status %r for %s — skipping", b.status, b.biomarker_id)
                continue
            feature_id = f"{base}_{suffix}"
            definition = self.binary_features.get(feature_id)  # None for *_normal
            features.append(GeneratedFeature(
                feature_id=feature_id,
                feature_type="BINARY",
                value=True,
                biomarker_id=b.biomarker_id,
                clinical_significance=definition.clinical_significance if definition else "",
                description=definition.description if definition else f"{base} within normal range",
                detail={"measured_value": b.value, "unit": b.unit, "status": b.status},
            ))
        return features

    # ── Severity ─────────────────────────────────────────────────────────────
    async def _generate_severity_features(
        self, biomarkers: List["NormalizedBiomarker"]
    ) -> List[GeneratedFeature]:
        """Severity band for each biomarker that has a SEVERITY definition."""
        by_code = {b.biomarker_id: b for b in biomarkers}
        features: List[GeneratedFeature] = []
        for feature_id, fd in self.severity_features.items():
            b = by_code.get(fd.biomarker_id)
            if b is None:
                logger.debug("Severity %s: biomarker %s missing — skipping", feature_id, fd.biomarker_id)
                continue
            band = SeverityClassifier.classify_severity(fd.biomarker_id, b.value)
            features.append(GeneratedFeature(
                feature_id=feature_id,
                feature_type="SEVERITY",
                value=band,
                biomarker_id=fd.biomarker_id,
                clinical_significance=fd.clinical_significance,
                description=fd.description,
                detail={"measured_value": b.value, "unit": fd.unit},
            ))
        return features

    # ── Ratios ───────────────────────────────────────────────────────────────
    async def _compute_ratio_features(
        self, biomarkers: List["NormalizedBiomarker"]
    ) -> List[GeneratedFeature]:
        """Compute ratio features; silently skip any with missing operands."""
        base_values = {
            CODE_TO_BASE[b.biomarker_id]: b.value
            for b in biomarkers
            if b.biomarker_id in CODE_TO_BASE and b.value is not None
        }
        features: List[GeneratedFeature] = []
        for feature_id, fd in self.ratio_features.items():
            method = (fd.calculation_method or "").strip()
            if "/" not in method:
                logger.debug("Ratio %s: unsupported method %r — skipping", feature_id, method)
                continue
            numerator_name, denominator_name = (p.strip() for p in method.split("/", 1))
            numerator = base_values.get(numerator_name)
            denominator = base_values.get(denominator_name)
            if numerator is None or denominator is None:
                logger.info("Ratio %s: missing operand(s) — skipping", feature_id)
                continue
            if denominator == 0:
                logger.info("Ratio %s: division by zero — skipping", feature_id)
                continue
            value = round(numerator / denominator, 4)
            features.append(GeneratedFeature(
                feature_id=feature_id,
                feature_type="RATIO",
                value=value,
                clinical_significance=fd.clinical_significance,
                description=fd.description,
                detail={"method": method, numerator_name: numerator, denominator_name: denominator},
            ))
        return features
