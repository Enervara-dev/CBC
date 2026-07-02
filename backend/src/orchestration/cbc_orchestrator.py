"""
CBC orchestrator — chains Layers 2→5 for a CBC panel.

    raw biomarker values  →  L2 normalize  →  L3 features  →  L4 graph reasoning
                          →  L5 validation  →  CBCAnalysisResult

Per-layer failures are isolated: only Layer 2 is fatal (no normalized values =
nothing to analyse); L3/L4/L5 failures degrade to ``partial`` and the pipeline
continues with what it has.

Interface adapters (the layer services predate this orchestrator):
  - L2 ``DataNormalizer.normalize(extracted_biomarkers, patient_metadata)`` —
    we adapt the ``{code: value}`` dict + gender/age into its expected inputs.
  - L3 ``FeatureGenerator.generate_features(normalized) -> List[GeneratedFeature]``
    (dataclasses) — we convert those + the L2 dataclasses into a Pydantic
    ``Layer3Output`` for L4.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from models.feature_schemas import (
    GeneratedFeature as PydGeneratedFeature,
    Layer3Output,
    PatternMatch as PydPatternMatch,
)
from models.graph_schemas import Layer4Output
from models.normalization_schemas import NormalizedBiomarker as PydNormalizedBiomarker
from models.validation_schemas import ClinicalFlag, FinalFinding, Layer5Output
from services.feature_generation.feature_definitions import FEATURE_REGISTRY

# Minimum biomarkers required to run an analysis (per-panel; from the domain).
from domains.cbc.biomarkers import REQUIRED_BIOMARKERS


# ─────────────────────────────────────────────────────────────────────────────
# Result + audit dataclasses
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class NestedAuditTrail:
    """Per-layer audit + timings for one analysis run."""

    timestamp: str
    patient_id: str
    biomarker_input_count: int
    layer2_normalization: Dict[str, Any]
    layer3_features: Dict[str, Any]
    layer4_graph_reasoning: Dict[str, Any]
    layer5_validation: Dict[str, Any]
    total_execution_ms: float
    layer_timings: Dict[str, float]
    # Populated by CBCFileAnalyzer when the run starts from a file (Layer 1 OCR).
    layer1_ocr: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CBCAnalysisResult:
    """The end-to-end result of a CBC analysis."""

    patient_id: str
    timestamp: str
    status: str                                   # success / partial / error
    final_findings: List[Any]                     # FinalFinding (L5) or ValidatedFinding (L4 fallback)
    clinical_flags: Dict[str, List[ClinicalFlag]]
    audit_trail: NestedAuditTrail
    error_messages: List[str]
    execution_time_ms: float
    recommendations: List[Any] = field(default_factory=list)  # Layer 4 recommendations (for Layer 6)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-ready dict."""
        def _dump(obj: Any) -> Any:
            return obj.model_dump() if hasattr(obj, "model_dump") else obj

        return {
            "patient_id": self.patient_id,
            "timestamp": self.timestamp,
            "status": self.status,
            "final_findings": [_dump(f) for f in self.final_findings],
            "clinical_flags": {k: [_dump(c) for c in v] for k, v in self.clinical_flags.items()},
            "recommendations": [_dump(r) for r in self.recommendations],
            "audit_trail": asdict(self.audit_trail),
            "error_messages": self.error_messages,
            "execution_time_ms": self.execution_time_ms,
        }

    def to_json(self) -> str:
        """Serialize to a JSON string."""
        return json.dumps(self.to_dict(), default=str, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────────
class CBCOrchestrator:
    """Async orchestrator chaining Layers 2→5 for a CBC panel."""

    def __init__(
        self,
        layer2_normalizer: Any,
        layer3_feature_generator: Any,
        layer4_graph_reasoner: Any,
        layer5_validator: Any,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.normalizer = layer2_normalizer
        self.feature_generator = layer3_feature_generator
        self.graph_reasoner = layer4_graph_reasoner
        self.validator = layer5_validator
        self.logger = logger or logging.getLogger(__name__)
        self.perf: Dict[str, float] = {}

    # ── Main orchestration ───────────────────────────────────────────────────
    async def analyze_cbc(
        self,
        biomarker_values: Dict[str, float],
        patient_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        gender: Optional[str] = None,
        age_years: Optional[int] = None,
        normalizer: Optional[Any] = None,
    ) -> CBCAnalysisResult:
        """
        Run the full L2→L5 pipeline for one patient's CBC values.

        ``normalizer`` lets the caller inject a per-request Layer-2 normalizer
        (bound to a fresh DB session) so concurrent requests never share one
        ``AsyncSession``. When omitted, the orchestrator's init-time
        ``self.normalizer`` is used (fine for single-threaded / CLI callers).
        """
        run_start = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        errors: List[str] = []
        timings: Dict[str, float] = {"layer2_ms": 0.0, "layer3_ms": 0.0,
                                     "layer4_ms": 0.0, "layer5_ms": 0.0}
        layers_ran: List[str] = []
        metadata = metadata or {}

        self.logger.info("CBC analysis start: patient=%s, %d biomarkers",
                         patient_id, len(biomarker_values))

        # STEP 0 — validate input
        valid, input_errors = await self._validate_inputs(biomarker_values)
        if not valid:
            self.logger.error("Input validation failed: %s", input_errors)
            return self._error_result(patient_id, started_at, input_errors,
                                       len(biomarker_values), timings, run_start)

        # Resolve the Layer-2 normalizer (per-request override, else init-time one).
        active_normalizer = normalizer or self.normalizer
        if active_normalizer is None:
            errors.append("layer2: no normalizer available (none injected and none configured)")
            self.logger.error(errors[-1])
            return self._error_result(patient_id, started_at, errors,
                                       len(biomarker_values), timings, run_start)

        # STEP 1 — Layer 2: normalization (fatal on failure)
        try:
            t0 = time.perf_counter()
            patient_metadata = {
                "gender": gender, "age": age_years,
                "lab_source": metadata.get("lab_name", "Any"),
            }
            extracted = [
                {"name": code, "value": value, "unit": "", "confidence": 1.0}
                for code, value in biomarker_values.items()
            ]
            layer2_output = await active_normalizer.normalize(extracted, patient_metadata)
            timings["layer2_ms"] = self._ms(t0)
            layers_ran.append("layer2")
            self.logger.info("Layer 2 done in %.2fms: %d biomarkers normalized",
                             timings["layer2_ms"], len(layer2_output))
        except Exception as exc:
            self.logger.error("Layer 2 failed: %s", exc)
            errors.append(f"layer2: {exc}")
            return self._error_result(patient_id, started_at, errors,
                                       len(biomarker_values), timings, run_start)

        if not layer2_output:
            errors.append("layer2: no biomarkers normalized")
            return self._error_result(patient_id, started_at, errors,
                                       len(biomarker_values), timings, run_start)

        # STEP 2 — Layer 3: feature generation (degrade on failure)
        layer3_output: Optional[Layer3Output] = None
        try:
            t0 = time.perf_counter()
            generated = await self.feature_generator.generate_features(layer2_output)
            layer3_output = self._build_layer3_output(layer2_output, generated)
            timings["layer3_ms"] = self._ms(t0)
            layers_ran.append("layer3")
            self.logger.info("Layer 3 done in %.2fms: %d features generated",
                             timings["layer3_ms"], len(layer3_output.generated_features))
        except Exception as exc:
            self.logger.error("Layer 3 failed: %s, continuing with defaults", exc)
            errors.append(f"layer3: {exc}")
            layer3_output = self._build_layer3_output(layer2_output, [])

        # STEP 3 — Layer 4: graph reasoning (optional on failure)
        layer4_output: Optional[Layer4Output] = None
        try:
            t0 = time.perf_counter()
            layer4_output = await self.graph_reasoner.reason(layer3_output)
            timings["layer4_ms"] = self._ms(t0)
            layers_ran.append("layer4")
            self.logger.info("Layer 4 done in %.2fms: %d findings, %d recommendations",
                             timings["layer4_ms"], len(layer4_output.validated_findings),
                             len(layer4_output.recommendations))
        except Exception as exc:
            self.logger.warning("Layer 4 failed (Neo4j issue?): %s, returning Layer 3 only", exc)
            errors.append(f"layer4: {exc}")
            layer4_output = None

        # STEP 4 — Layer 5: confidence validation (optional on failure)
        layer5_output: Optional[Layer5Output] = None
        if layer4_output is None:
            self.logger.info("Layer 5 skipped: no Layer 4 output to validate")
        else:
            try:
                t0 = time.perf_counter()
                layer5_output = await self.validator.validate(layer4_output)
                timings["layer5_ms"] = self._ms(t0)
                layers_ran.append("layer5")
                flag_count = sum(len(v) for v in layer5_output.clinical_flags.values())
                self.logger.info("Layer 5 done in %.2fms: %d final findings, %d flags",
                                 timings["layer5_ms"], len(layer5_output.final_findings), flag_count)
            except Exception as exc:
                self.logger.error("Layer 5 failed: %s, returning Layer 4 output", exc)
                errors.append(f"layer5: {exc}")
                layer5_output = None

        # STEP 5 — thread audit trail + build result
        audit_trail = await self._build_nested_audit_trail(
            layer2_output, layer3_output, layer4_output, layer5_output, timings, patient_id,
            len(biomarker_values),
        )

        if layer5_output is not None:
            final_findings: List[Any] = layer5_output.final_findings
            clinical_flags = layer5_output.clinical_flags
        elif layer4_output is not None:
            final_findings = layer4_output.validated_findings
            clinical_flags = {}
        else:
            final_findings = []
            clinical_flags = {}

        recommendations = layer4_output.recommendations if layer4_output is not None else []

        status = self._determine_status(len(errors), layers_ran)
        total_ms = self._ms(run_start)
        flag_total = sum(len(v) for v in clinical_flags.values())
        self.logger.info(
            "CBC analysis complete (status=%s, patient=%s, time=%.2fms, findings=%d, flags=%d)",
            status, patient_id, total_ms, len(final_findings), flag_total,
        )

        return CBCAnalysisResult(
            patient_id=patient_id, timestamp=started_at, status=status,
            final_findings=final_findings, clinical_flags=clinical_flags,
            recommendations=recommendations,
            audit_trail=audit_trail, error_messages=errors, execution_time_ms=total_ms,
        )

    # ── Helpers ──────────────────────────────────────────────────────────────
    async def _validate_inputs(
        self, biomarker_values: Dict[str, float]
    ) -> Tuple[bool, List[str]]:
        """Check required CBC biomarkers are present and numeric."""
        self.logger.debug("Validating inputs: %d biomarkers provided", len(biomarker_values))
        errors: List[str] = []
        if not biomarker_values:
            return False, ["No biomarker values provided."]
        missing = [b for b in REQUIRED_BIOMARKERS if b not in biomarker_values]
        if missing:
            errors.append(f"Missing required biomarkers: {', '.join(missing)}.")
        for code, value in biomarker_values.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(f"Biomarker {code} has non-numeric value: {value!r}.")
        return (not errors), errors

    async def _build_nested_audit_trail(
        self,
        layer2_output: List[Any],
        layer3_output: Optional[Layer3Output],
        layer4_output: Optional[Layer4Output],
        layer5_output: Optional[Layer5Output],
        timings: Dict[str, float],
        patient_id: str,
        biomarker_input_count: int,
    ) -> NestedAuditTrail:
        """Combine each layer's audit (or an empty dict if it failed)."""
        quality_issues = sum(len(getattr(nb, "quality_issues", []) or []) for nb in layer2_output)
        layer2_audit = {
            "biomarkers_normalized": len(layer2_output),
            "quality_issues": quality_issues,
            "execution_ms": timings["layer2_ms"],
        }
        layer3_audit = {
            "binary_features_generated": (
                len(layer3_output.generated_features) if layer3_output else 0
            ),
            "patterns_detected": (
                len(layer3_output.detected_patterns) if layer3_output else 0
            ),
            "execution_ms": timings["layer3_ms"],
        }
        return NestedAuditTrail(
            timestamp=datetime.now(timezone.utc).isoformat(),
            patient_id=patient_id,
            biomarker_input_count=biomarker_input_count,
            layer2_normalization=layer2_audit,
            layer3_features=layer3_audit,
            layer4_graph_reasoning=self._get_layer_audit(layer4_output),
            layer5_validation=self._get_layer_audit(layer5_output),
            total_execution_ms=sum(timings.values()),
            layer_timings=dict(timings),
        )

    @staticmethod
    def _get_layer_audit(layer_output: Any) -> Dict[str, Any]:
        """Extract a layer's audit_trail as a dict, or {} if absent."""
        audit = getattr(layer_output, "audit_trail", None)
        if audit is None:
            return {}
        return audit.model_dump() if hasattr(audit, "model_dump") else dict(audit)

    @staticmethod
    def _determine_status(error_count: int, layers_ran: List[str]) -> str:
        """success (no errors) / error (L2 never ran) / partial (some errors)."""
        if "layer2" not in layers_ran:
            return "error"
        if error_count > 0:
            return "partial"
        return "success"

    # ── dataclass(L2/L3) → Pydantic Layer3Output conversion ──────────────────
    def _build_layer3_output(
        self, layer2_output: List[Any], generated_features: List[Any]
    ) -> Layer3Output:
        """Convert L2/L3 dataclasses into the Pydantic Layer3Output for L4."""
        pyd_biomarkers = [self._to_pyd_biomarker(nb) for nb in layer2_output]
        pyd_features, patterns = self._convert_features(generated_features)
        return Layer3Output(
            normalized_biomarkers=pyd_biomarkers,
            generated_features=pyd_features,
            detected_patterns=patterns,
        )

    @staticmethod
    def _to_pyd_biomarker(nb: Any) -> PydNormalizedBiomarker:
        """dataclass NormalizedBiomarker → Pydantic schema (coalescing Nones)."""
        return PydNormalizedBiomarker(
            biomarker_id=nb.biomarker_id,
            biomarker_name=getattr(nb, "biomarker_name", None) or nb.biomarker_id,
            value=nb.value,
            unit=nb.unit or "",
            reference_min=nb.reference_min if nb.reference_min is not None else 0.0,
            reference_max=nb.reference_max if nb.reference_max is not None else 0.0,
            gender=nb.gender or "U",
            age_group=nb.age_group or "adult",
            lab_source=nb.lab_source or "Any",
            status=nb.status or "NORMAL",
            deviation_from_min=nb.deviation_from_min if nb.deviation_from_min is not None else 0.0,
            deviation_percent=nb.deviation_percent if nb.deviation_percent is not None else 0.0,
            extraction_confidence=(
                nb.extraction_confidence if nb.extraction_confidence is not None else 1.0
            ),
            quality_issues=list(nb.quality_issues or []),
            critical_flag=bool(getattr(nb, "critical_flag", False)),
            loinc_code=getattr(nb, "loinc_code", None),
        )

    @staticmethod
    def _convert_features(
        generated_features: List[Any],
    ) -> Tuple[List[PydGeneratedFeature], List[PydPatternMatch]]:
        """dataclass GeneratedFeature[] → (Pydantic features, Pydantic patterns)."""
        features: List[PydGeneratedFeature] = []
        patterns: List[PydPatternMatch] = []
        for gf in generated_features:
            is_pattern = gf.feature_type == "PATTERN"
            detail = getattr(gf, "detail", None) or {}
            features.append(PydGeneratedFeature(
                feature_id=gf.feature_id,
                feature_name=getattr(gf, "feature_id", ""),
                feature_type=gf.feature_type,
                value=gf.value,
                severity=gf.value if gf.feature_type == "SEVERITY" else None,
                confidence=gf.value if is_pattern and isinstance(gf.value, (int, float)) else 1.0,
                clinical_significance=getattr(gf, "clinical_significance", ""),
                source_biomarkers=[gf.biomarker_id] if getattr(gf, "biomarker_id", None) else [],
                calculation_method=detail.get("method"),
            ))
            if is_pattern:
                definition = FEATURE_REGISTRY.get(gf.feature_id)
                patterns.append(PydPatternMatch(
                    pattern_id=gf.feature_id,
                    pattern_name=detail.get("pattern_name", gf.feature_id),
                    feature_composition=(definition.feature_composition if definition else []) or [],
                    matched_features=detail.get("matched_features", []),
                    missing_features=detail.get("missing_features", []),
                    match_percentage=detail.get("match_percentage", 0.0),
                    confidence=detail.get(
                        "confidence", gf.value if isinstance(gf.value, (int, float)) else 0.0
                    ),
                ))
        return features, patterns

    # ── small utilities ──────────────────────────────────────────────────────
    @staticmethod
    def _ms(start: float) -> float:
        return round((time.perf_counter() - start) * 1000.0, 3)

    def _error_result(
        self,
        patient_id: str,
        started_at: str,
        errors: List[str],
        biomarker_input_count: int,
        timings: Dict[str, float],
        run_start: float,
    ) -> CBCAnalysisResult:
        """Build a terminal error result (input invalid or Layer 2 failed)."""
        audit = NestedAuditTrail(
            timestamp=datetime.now(timezone.utc).isoformat(),
            patient_id=patient_id,
            biomarker_input_count=biomarker_input_count,
            layer2_normalization={"execution_ms": timings["layer2_ms"]},
            layer3_features={}, layer4_graph_reasoning={}, layer5_validation={},
            total_execution_ms=sum(timings.values()),
            layer_timings=dict(timings),
        )
        return CBCAnalysisResult(
            patient_id=patient_id, timestamp=started_at, status="error",
            final_findings=[], clinical_flags={}, audit_trail=audit,
            error_messages=errors, execution_time_ms=self._ms(run_start),
        )
