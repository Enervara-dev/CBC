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

from domains.registry import merged_code_to_name
from services.feature_generation.feature_definitions import FeatureDefinition
from services.feature_generation.severity_classifier import SeverityClassifier

if TYPE_CHECKING:  # type-only import (keeps sqlalchemy out of this module)
    from services.normalization.normalizer import NormalizedBiomarker

logger = logging.getLogger(__name__)


# Canonical biomarker code → the base name used in feature ids / ratio formulas
# (``HGB`` → ``hemoglobin`` → ``hemoglobin_low``). This is exactly each domain's
# ``code_to_name`` table, merged across every registered panel.
CODE_TO_BASE: Dict[str, str] = merged_code_to_name()

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
        """
        Compute ratio features; silently skip any with missing operands.

        Operands are canonical names (``ast / alt``). ``<name>_xuln`` is the value
        as a multiple of *this patient's* upper reference limit, which is how liver
        enzymes are interpreted (the R factor, "ALP > 1.5 x ULN") and why it follows
        the sex/age/pregnancy range Layer 2 applied. A method without ``/`` is a
        single operand, so ``alp_xuln`` on its own is a feature.

        A ratio whose definition carries a threshold also emits a BINARY fact —
        ``<id>_high`` when value > ``threshold_low``, ``<id>_low`` when value <
        ``threshold_high`` (the binary threshold convention) — because Layer 4
        conditions match binary facts, and "AST/ALT > 2" is exactly such a fact.
        """
        base_values: Dict[str, float] = {}
        for b in biomarkers:
            base = CODE_TO_BASE.get(b.biomarker_id)
            if base is None or b.value is None:
                continue
            base_values[base] = b.value
            upper = getattr(b, "reference_max", None)
            if upper:   # one-sided ranges (lipids) have no upper limit to scale by
                base_values[f"{base}_xuln"] = b.value / upper

        features: List[GeneratedFeature] = []
        for feature_id, fd in self.ratio_features.items():
            method = (fd.calculation_method or "").strip()
            if not method:
                continue
            names = [p.strip() for p in method.split("/", 1)]
            operands = {name: base_values.get(name) for name in names}
            if any(v is None for v in operands.values()):
                logger.info("Ratio %s: missing operand(s) — skipping", feature_id)
                continue
            if len(names) == 2:
                if operands[names[1]] == 0:
                    logger.info("Ratio %s: division by zero — skipping", feature_id)
                    continue
                value = round(operands[names[0]] / operands[names[1]], 4)
            else:
                value = round(operands[names[0]], 4)
            detail = {"method": method, **operands}
            features.append(GeneratedFeature(
                feature_id=feature_id,
                feature_type="RATIO",
                value=value,
                clinical_significance=fd.clinical_significance,
                description=fd.description,
                detail=detail,
            ))
            features.extend(self._ratio_facts(feature_id, fd, value, detail))
        return features

    @staticmethod
    def _ratio_facts(
        feature_id: str, fd: FeatureDefinition, value: float, detail: Dict[str, Any]
    ) -> List[GeneratedFeature]:
        """Binary facts for a ratio that crossed its declared threshold(s)."""
        crossed = []
        if fd.threshold_low is not None and value > fd.threshold_low:
            crossed.append((f"{feature_id}_high", ">", fd.threshold_low))
        if fd.threshold_high is not None and value < fd.threshold_high:
            crossed.append((f"{feature_id}_low", "<", fd.threshold_high))
        return [
            GeneratedFeature(
                feature_id=fact_id,
                feature_type="BINARY",
                value=True,
                clinical_significance=fd.clinical_significance,
                description=f"{fd.feature_name} {op} {cutoff:g} (value {value:g})",
                detail={**detail, "value": value, "cutoff": cutoff},
            )
            for fact_id, op, cutoff in crossed
        ]
