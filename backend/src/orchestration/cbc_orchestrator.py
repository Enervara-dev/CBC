"""
Analysis orchestrator — chains Layers 2→5 for a lab panel.

    raw biomarker values  →  L2 normalize  →  L3 features  →  L4 graph reasoning
                          →  L5 validation  →  CBCAnalysisResult

Per-layer failures are isolated: only Layer 2 is fatal (no normalized values =
nothing to analyse); L3/L4/L5 failures degrade to ``partial`` and the pipeline
continues with what it has.

Panel handling
--------------
The orchestrator is panel-agnostic: it asks the domain registry which panel(s)
the submitted codes belong to (:func:`domains.registry.detect_panels`) and
enforces *that* panel's ``required_biomarkers``. A CBC panel is still checked for
HGB/MCV/RDW/WBC/PLT; a liver panel is checked for ALT/AST/ALP/TBIL/ALB; a mixed
report is checked against its dominant panel. The class keeps its ``CBC`` name
and ``analyze_cbc`` entry point for API compatibility.

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

# Which panel a set of codes belongs to, that panel's required biomarkers, and
# the feature library spanning every registered panel.
from domains.registry import detect_panels, get_domain, merged_feature_registry

FEATURE_REGISTRY = merged_feature_registry()


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
    # Values the pipeline computed rather than read (ANC/ALC from WBC x %), so a
    # reader can tell a reported absolute count from a derived one.
    derived_biomarkers: List[Dict[str, Any]] = field(default_factory=list)
    # Reference-range condition applied to this patient (e.g. "pregnancy").
    patient_condition: Optional[str] = None


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
# Differential counts printed as percentages are relative: neutrophils 38% is
# neutropenia at a WBC of 3.0 and normal at 12.0. Infection risk and every
# grading scheme (CTCAE, IDSA) use the absolute count, so it is derived whenever
# a report gives only the WBC and the percentage.
_ABSOLUTE_FROM_PERCENT: Dict[str, str] = {"ANC": "NEUT", "ALC": "LYMPH"}


def _derive_absolute_counts(
    values: Dict[str, float], units: Dict[str, str]
) -> Tuple[Dict[str, float], List[Dict[str, Any]]]:
    """
    Add ANC/ALC computed from WBC x percentage when the report omits them.

    The absolute count inherits the WBC's unit (``units`` is updated in place),
    so "8000 cells/cumm x 58%" converts exactly like the WBC does. A differential
    whose unit is not a percentage, or whose value cannot be one, is left alone
    rather than guessed at.
    """
    out = dict(values)
    derived: List[Dict[str, Any]] = []
    wbc = values.get("WBC")
    if wbc is None:
        return out, derived
    for absolute, percent in _ABSOLUTE_FROM_PERCENT.items():
        pct = values.get(percent)
        if absolute in values or pct is None:
            continue
        if (units.get(percent) or "%").strip() != "%" or not 0.0 <= pct <= 100.0:
            continue
        out[absolute] = round(wbc * pct / 100.0, 4)
        if units.get("WBC"):
            units[absolute] = units["WBC"]
        derived.append({"code": absolute, "from": ["WBC", percent],
                        "value": out[absolute], "unit": units.get("WBC", "")})
    return out, derived


def _patient_condition(metadata: Dict[str, Any]) -> Optional[str]:
    """The reference-range ``condition`` for this patient (``"pregnancy"`` or None)."""
    raw = "pregnancy" if metadata.get("pregnant") is True else metadata.get("condition")
    if not raw:
        return None
    text = str(raw).strip().lower()
    return "pregnancy" if text in ("pregnancy", "pregnant") else text


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
        errors.extend(self._incomplete_panel_notes(biomarker_values))

        # Resolve the Layer-2 normalizer (per-request override, else init-time one).
        active_normalizer = normalizer or self.normalizer
        if active_normalizer is None:
            errors.append("layer2: no normalizer available (none injected and none configured)")
            self.logger.error(errors[-1])
            return self._error_result(patient_id, started_at, errors,
                                       len(biomarker_values), timings, run_start)

        derived: List[Dict[str, Any]] = []
        # STEP 1 — Layer 2: normalization (fatal on failure)
        try:
            t0 = time.perf_counter()
            patient_metadata = {
                "gender": gender, "age": age_years,
                "lab_source": metadata.get("lab_name", "Any"),
                # Pregnancy changes what is normal: haemodilution lowers haemoglobin
                # and albumin, and placental ALP can double. Without it a healthy
                # third-trimester ALP of 210 was flagged as cholestasis.
                "condition": _patient_condition(metadata),
            }
            # Units come from Layer 1 via the adapter's metadata (``units``). They
            # matter: without them Layer 2 assumes every value is already in the
            # standard unit, so a platelet count printed "2.91 lakhs/cumm" is read
            # as 2.91 K/uL and flagged as critical thrombocytopenia. The JSON
            # ``/api/analyze`` route sends no units and keeps the old assumption.
            ocr_units: Dict[str, str] = dict(metadata.get("units") or {})
            values, derived = _derive_absolute_counts(biomarker_values, ocr_units)
            extracted = [
                {"name": code, "value": value, "unit": ocr_units.get(code, ""),
                 "confidence": 1.0}
                for code, value in values.items()
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
            # Say why: "no biomarkers normalized" alone hid a database outage.
            causes = list(getattr(active_normalizer, "validation_issues", None) or [])[:5]
            errors.append("layer2: no biomarkers normalized"
                          + (f" — {'; '.join(causes)}" if causes else ""))
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
            len(biomarker_values), metadata.get("repeated_biomarkers") or {},
        )
        audit_trail.derived_biomarkers = derived
        audit_trail.patient_condition = patient_metadata.get("condition")

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
        """
        Check the panel is recognised, substantially present, and numeric.

        The required-biomarker set is the *detected* panel's, not always CBC's:
        a lipid profile must not be rejected for lacking haemoglobin. Codes
        belonging to other panels are welcome extras — a mixed report is
        validated against its dominant panel.

        Only a panel with fewer than half of its core markers is refused (a
        prescription whose one stray match reads as "Hb"). A merely incomplete
        panel is analysed — see :meth:`_incomplete_panel_notes`.
        """
        self.logger.debug("Validating inputs: %d biomarkers provided", len(biomarker_values))
        errors: List[str] = []
        if not biomarker_values:
            return False, ["No biomarker values provided."]

        detection = detect_panels(biomarker_values)
        if not detection.primary:
            return False, [
                "No recognised biomarkers: "
                f"{', '.join(detection.unknown)} match no registered panel."
            ]
        for panel_key in detection.primary:
            panel = get_domain(panel_key)
            required = panel.required_biomarkers
            present = [b for b in required if b in biomarker_values]
            if len(present) * 2 < len(required):
                errors.append(
                    f"Too few biomarkers for the {panel.name} panel: need at least "
                    f"{-(-len(required) // 2)} of {', '.join(required)}; got "
                    f"{', '.join(present) or 'none'}."
                )
        if detection.unknown:
            self.logger.info("Ignoring unrecognised biomarker code(s): %s",
                             ", ".join(detection.unknown))

        for code, value in biomarker_values.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(f"Biomarker {code} has non-numeric value: {value!r}.")
        return (not errors), errors

    @staticmethod
    def _incomplete_panel_notes(biomarker_values: Dict[str, float]) -> List[str]:
        """
        Name the core markers a detected panel lacks, without refusing to analyse.

        Incomplete panels are clinically ordinary: laboratories omit a calculated
        LDL when triglycerides exceed 400 mg/dL, and "liver enzymes" often arrive
        without albumin. Rejecting such a report discarded exactly the values that
        matter most — a triglyceride of 700, an ALT of 3000 — so the analysis runs
        on what is present and says what was missing (status ``partial``).
        """
        notes: List[str] = []
        for panel_key in detect_panels(biomarker_values).primary:
            panel = get_domain(panel_key)
            missing = [b for b in panel.required_biomarkers if b not in biomarker_values]
            if missing:
                notes.append(
                    f"Incomplete {panel.name} panel: {', '.join(missing)} not reported — "
                    "analysed the markers present."
                )
        return notes

    async def _build_nested_audit_trail(
        self,
        layer2_output: List[Any],
        layer3_output: Optional[Layer3Output],
        layer4_output: Optional[Layer4Output],
        layer5_output: Optional[Layer5Output],
        timings: Dict[str, float],
        patient_id: str,
        biomarker_input_count: int,
        repeated_biomarkers: Optional[Dict[str, List[float]]] = None,
    ) -> NestedAuditTrail:
        """Combine each layer's audit (or an empty dict if it failed)."""
        quality_issues = sum(len(getattr(nb, "quality_issues", []) or []) for nb in layer2_output)
        detection = detect_panels(nb.biomarker_id for nb in layer2_output)
        layer2_audit = {
            "biomarkers_normalized": len(layer2_output),
            "quality_issues": quality_issues,
            "panels_detected": detection.matched,
            "primary_panel": detection.primary,
            "execution_ms": timings["layer2_ms"],
        }
        # A document holding two draws is analysed on its first panel only; say so
        # rather than presenting the blend as one result set.
        if repeated_biomarkers:
            layer2_audit["repeated_biomarkers"] = repeated_biomarkers
            layer2_audit["repeated_biomarkers_note"] = (
                "These biomarkers were resolved from more than one line of the source "
                "document; only the first occurrence of each was analysed. The document "
                "may contain multiple panels or draws, or another row (a ratio, an "
                "absolute count, a method or reference-range line) may have resolved to "
                "the same code. Worth a human check."
            )
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
