"""
Comprehensive integration tests for Layer 3 (Feature Generation).

Suites:
  - TestFeatureDefinitions          the feature library is well-formed
  - TestFeatureGenerator            async binary/severity/ratio/pattern generation
  - TestPatternMatcher              async pattern matching + confidence bands
  - TestSeverityClassifier          value → severity band
  - TestFeatureGenerationIntegration end-to-end biomarkers → features → schema

No database is needed — Layer 3 is pure in-process logic over Layer 2 dataclasses.
"""

import pytest
from datetime import datetime, timezone

from services.normalization.normalizer import NormalizedBiomarker
from services.feature_generation.feature_definitions import (
    FEATURE_REGISTRY, BINARY_FEATURES, SEVERITY_FEATURES, COMPUTED_RATIO_FEATURES,
    PATTERN_FEATURES, ANEMIA_SUBTYPE_PATTERNS, VALID_FEATURE_TYPES,
    FeatureDefinition, get_feature, list_all_features, get_features_by_type,
    get_patterns, validate_feature_definition,
)
from services.feature_generation.feature_generator import FeatureGenerator, GeneratedFeature
from services.feature_generation.severity_classifier import SeverityClassifier
from models.feature_schemas import (
    FeatureType, SeverityLevel, FeatureGenerationResult,
    GeneratedFeature as GeneratedFeatureSchema,
    PatternMatch as PatternMatchSchema,
)


# ── Builders / helpers ──────────────────────────────────────────────────────
def _nb(code: str, value: float, status: str, unit: str = "") -> NormalizedBiomarker:
    """Construct a NormalizedBiomarker (Layer 2 output) for tests."""
    return NormalizedBiomarker(
        biomarker_id=code, value=value, unit=unit,
        reference_min=0.0, reference_max=0.0, gender="F", age_group="adult",
        lab_source="Any", status=status, deviation_from_min=0.0,
        deviation_percent=0.0, extraction_confidence=0.95,
    )


def _bin(feature_id: str) -> GeneratedFeature:
    """A present binary generated feature (value True)."""
    return GeneratedFeature(feature_id=feature_id, feature_type="BINARY", value=True)


def _to_schema(gf: GeneratedFeature) -> GeneratedFeatureSchema:
    """Convert a service GeneratedFeature dataclass to its Pydantic schema."""
    fd = FEATURE_REGISTRY.get(gf.feature_id)
    return GeneratedFeatureSchema(
        feature_id=gf.feature_id,
        feature_name=fd.feature_name if fd else gf.feature_id,
        feature_type=gf.feature_type,
        value=gf.value,
        severity=gf.value if gf.feature_type == "SEVERITY" else None,
        confidence=gf.value if gf.feature_type == "PATTERN" else 1.0,
        clinical_significance=gf.clinical_significance,
        source_biomarkers=[gf.biomarker_id] if gf.biomarker_id else [],
        calculation_method=fd.calculation_method if fd else None,
    )


def _to_pattern_schema(gf: GeneratedFeature) -> PatternMatchSchema:
    """Convert a PATTERN GeneratedFeature to the PatternMatch schema."""
    detail = gf.detail or {}
    fd = FEATURE_REGISTRY.get(gf.feature_id)
    return PatternMatchSchema(
        pattern_id=gf.feature_id,
        pattern_name=detail.get("pattern_name", gf.feature_id),
        feature_composition=(fd.feature_composition if fd else []) or [],
        matched_features=detail.get("matched_features", []),
        missing_features=detail.get("missing_features", []),
        match_percentage=detail.get("match_percentage", 0.0),
        confidence=detail.get("confidence", gf.value),
    )


# ── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture
def feature_definitions():
    """The full feature library registry."""
    return FEATURE_REGISTRY


@pytest.fixture
def feature_generator():
    """A FeatureGenerator wired with the full library."""
    return FeatureGenerator(FEATURE_REGISTRY)


@pytest.fixture
def severity_classifier():
    """The (static) severity classifier."""
    return SeverityClassifier


@pytest.fixture
def sample_normalized_biomarkers():
    """A mostly-normal patient."""
    return [
        _nb("HGB", 14.0, "NORMAL", "g/dL"),
        _nb("WBC", 7.0, "NORMAL", "K/uL"),
        _nb("PLT", 250.0, "NORMAL", "K/uL"),
        _nb("MCV", 90.0, "NORMAL", "fL"),
        _nb("RBC", 4.8, "NORMAL", "M/uL"),
        _nb("RDW", 13.0, "NORMAL", "%"),
    ]


@pytest.fixture
def sample_abnormal_biomarkers():
    """Iron-deficiency microcytic anemia + bacterial infection."""
    return [
        _nb("HGB", 9.5, "LOW", "g/dL"),
        _nb("MCV", 72.0, "LOW", "fL"),
        _nb("RDW", 16.0, "HIGH", "%"),
        _nb("RBC", 4.0, "LOW", "M/uL"),
        _nb("WBC", 13.0, "HIGH", "K/uL"),
        _nb("NEUT", 75.0, "HIGH", "%"),
        _nb("LYMPH", 15.0, "LOW", "%"),
        _nb("PLT", 250.0, "NORMAL", "K/uL"),
    ]


# ── TestFeatureDefinitions ──────────────────────────────────────────────────
class TestFeatureDefinitions:
    def test_all_features_have_ids(self, feature_definitions):
        """Every registry key matches its feature's non-empty feature_id."""
        for fid, fd in feature_definitions.items():
            assert fd.feature_id == fid and fd.feature_id

    def test_feature_type_validity(self, feature_definitions):
        """Every feature's type is one of the valid types and passes validation."""
        for fd in feature_definitions.values():
            assert fd.feature_type in VALID_FEATURE_TYPES
            assert validate_feature_definition(fd) is True

    def test_pattern_features_have_composition(self):
        """Every PATTERN feature has a non-empty feature_composition."""
        for fd in get_patterns():
            assert fd.feature_composition and len(fd.feature_composition) >= 1

    def test_severity_features_have_levels(self):
        """SEVERITY features have levels + ranges, and bands are named levels."""
        for fd in SEVERITY_FEATURES.values():
            assert fd.severity_levels and fd.threshold_ranges
            assert set(fd.threshold_ranges).issubset(set(fd.severity_levels))

    def test_get_feature_function(self):
        """get_feature returns the right definition and raises on unknown ids."""
        assert get_feature("hemoglobin_low").biomarker_id == "HGB"
        with pytest.raises(KeyError):
            get_feature("does_not_exist")

    def test_list_features_by_type(self):
        """Type filters partition the registry consistently."""
        assert len(get_features_by_type("BINARY")) == len(BINARY_FEATURES)
        assert len(get_features_by_type("RATIO")) == len(COMPUTED_RATIO_FEATURES)
        assert len(get_patterns()) == len(PATTERN_FEATURES) + len(ANEMIA_SUBTYPE_PATTERNS)
        assert len(list_all_features()) == len(FEATURE_REGISTRY)


# ── TestFeatureGenerator ────────────────────────────────────────────────────
class TestFeatureGenerator:
    async def test_generate_binary_features(self, feature_generator, sample_abnormal_biomarkers):
        """Binary features reflect each biomarker's status, all value=True."""
        feats = await feature_generator._generate_binary_features(sample_abnormal_biomarkers)
        ids = {f.feature_id for f in feats}
        assert {"hemoglobin_low", "mcv_low", "rdw_high", "wbc_high"} <= ids
        assert all(f.value is True and f.feature_type == "BINARY" for f in feats)

    async def test_generate_severity_features(self, feature_generator):
        """Hemoglobin 9.5 g/dL grades as 'moderate'."""
        feats = await feature_generator._generate_severity_features([_nb("HGB", 9.5, "LOW")])
        sev = {f.feature_id: f.value for f in feats}
        assert sev["hemoglobin_severity"] == "moderate"

    async def test_compute_ratio_features(self, feature_generator):
        """rbc_index = hemoglobin / rbc = 9.5 / 4.0 = 2.375."""
        feats = await feature_generator._compute_ratio_features(
            [_nb("HGB", 9.5, "LOW"), _nb("RBC", 4.0, "LOW")]
        )
        ratios = {f.feature_id: f.value for f in feats}
        assert ratios["rbc_index"] == 2.375

    async def test_no_disease_inference_in_layer3(self, feature_generator, sample_abnormal_biomarkers):
        """Layer 3 emits facts only — NO PATTERN features (inference is Layer 4)."""
        feats = await feature_generator.generate_features(sample_abnormal_biomarkers)
        assert all(f.feature_type != "PATTERN" for f in feats)
        assert not hasattr(feature_generator, "_detect_pattern_features")

    async def test_handle_missing_biomarkers(self, feature_generator):
        """A ratio needing an absent biomarker is skipped; missing is tracked."""
        feats = await feature_generator.generate_features([_nb("HGB", 9.5, "LOW")])
        ids = {f.feature_id for f in feats}
        assert "rbc_index" not in ids          # needs RBC, which is absent
        assert "RBC" in feature_generator.missing_biomarkers

    async def test_partial_feature_generation(self, feature_generator):
        """An unmappable biomarker is skipped while known ones still generate."""
        feats = await feature_generator.generate_features(
            [_nb("HGB", 9.5, "LOW"), _nb("GLU", 100.0, "HIGH")]
        )
        ids = {f.feature_id for f in feats}
        assert "hemoglobin_low" in ids
        assert not any("glu" in i for i in ids)

    async def test_with_multiple_biomarkers(self, feature_generator, sample_abnormal_biomarkers):
        """The three fact types are produced for a rich abnormal panel (no patterns)."""
        feats = await feature_generator.generate_features(sample_abnormal_biomarkers)
        assert {f.feature_type for f in feats} == {"BINARY", "SEVERITY", "RATIO"}

    async def test_feature_count_reasonable(self, feature_generator, sample_abnormal_biomarkers):
        """The generated feature count is in a sensible range."""
        feats = await feature_generator.generate_features(sample_abnormal_biomarkers)
        assert 8 <= len(feats) <= 100


# ── TestSeverityClassifier ──────────────────────────────────────────────────
class TestSeverityClassifier:
    def test_critical_severity(self, severity_classifier):
        """HGB 2.0 g/dL → critical."""
        assert severity_classifier.classify_severity("HGB", 2.0) == "critical"

    def test_severe_severity(self, severity_classifier):
        """HGB 5.0 g/dL → severe."""
        assert severity_classifier.classify_severity("HGB", 5.0) == "severe"

    def test_moderate_severity(self, severity_classifier):
        """HGB 8.0 g/dL → moderate."""
        assert severity_classifier.classify_severity("HGB", 8.0) == "moderate"

    def test_mild_severity(self, severity_classifier):
        """HGB 11.0 g/dL → mild."""
        assert severity_classifier.classify_severity("HGB", 11.0) == "mild"

    def test_normal_severity(self, severity_classifier):
        """HGB 14.0 g/dL → normal."""
        assert severity_classifier.classify_severity("HGB", 14.0) == "normal"

    def test_classify_all(self, severity_classifier):
        """classify_all returns a band only for biomarkers with severity defs."""
        result = severity_classifier.classify_all(
            [_nb("HGB", 8.0, "LOW"), _nb("WBC", 7.0, "NORMAL"),
             _nb("PLT", 15.0, "LOW"), _nb("MCV", 90.0, "NORMAL")]
        )
        assert result == {"HGB": "moderate", "WBC": "normal", "PLT": "severe"}
        assert "MCV" not in result  # MCV has no severity definition

    def test_unknown_biomarker_returns_normal(self, severity_classifier):
        """A biomarker with no severity definition classifies as 'normal'."""
        assert severity_classifier.classify_severity("MCV", 90.0) == "normal"

    def test_severity_clamps_out_of_range(self, severity_classifier):
        """Values beyond the band extremes clamp to the nearest band."""
        assert severity_classifier.classify_severity("HGB", 25.0) == "normal"   # above ceiling
        assert severity_classifier.classify_severity("HGB", -1.0) == "critical"  # below floor


# ── TestFeatureGenerationIntegration ────────────────────────────────────────
class TestFeatureGenerationIntegration:
    async def test_full_pipeline_from_biomarkers(self, feature_generator, sample_abnormal_biomarkers):
        """End-to-end run produces at least one feature of every FACT type."""
        feats = await feature_generator.generate_features(sample_abnormal_biomarkers)
        for t in ("BINARY", "SEVERITY", "RATIO"):
            assert any(f.feature_type == t for f in feats)

    async def test_emits_facts_not_diseases(self, feature_generator, sample_abnormal_biomarkers):
        """The binary FACTS feeding Layer 4 are present; no disease patterns are emitted."""
        feats = await feature_generator.generate_features(sample_abnormal_biomarkers)
        binary_facts = {f.feature_id for f in feats if f.feature_type == "BINARY"}
        # The facts Layer 4 will traverse the knowledge graph with:
        assert {"hemoglobin_low", "mcv_low", "rdw_high", "wbc_high", "neutrophils_high"} <= binary_facts
        # No disease inference happens in Layer 3:
        assert not any(f.feature_type == "PATTERN" for f in feats)
        assert "iron_deficiency_anemia" not in {f.feature_id for f in feats}

    async def test_binary_facts_carry_source_biomarker(self, feature_generator, sample_abnormal_biomarkers):
        """Binary facts retain their biomarker_id so Layer 4 can trace evidence."""
        feats = await feature_generator.generate_features(sample_abnormal_biomarkers)
        hgb_low = next(f for f in feats if f.feature_id == "hemoglobin_low")
        assert hgb_low.biomarker_id == "HGB"

    async def test_output_format_valid(self, feature_generator, sample_abnormal_biomarkers):
        """Generated features serialize cleanly into the Pydantic Layer 3 schemas."""
        feats = await feature_generator.generate_features(sample_abnormal_biomarkers)
        schema_features = [_to_schema(f) for f in feats]
        pattern_schemas = [_to_pattern_schema(f) for f in feats if f.feature_type == "PATTERN"]
        result = FeatureGenerationResult(
            status="success",
            generated_features=schema_features,
            detected_patterns=pattern_schemas,
            total_features_generated=len(feats),
            generation_timestamp=datetime.now(timezone.utc).isoformat(),
        )
        assert result.total_features_generated == len(feats)
        assert all(isinstance(f.feature_type, FeatureType) for f in result.generated_features)
        assert all(0.0 <= p.confidence <= 1.0 for p in result.detected_patterns)
