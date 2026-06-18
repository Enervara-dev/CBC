"""
Comprehensive integration tests for Layer 2 (Data Normalization).

Suites:
  - TestUnitConverter            unit-conversion math + validation
  - TestReferenceRangeLookup     async demographic priority-fallback lookups
  - TestDataNormalizer           async orchestration (convert + lookup + quality)
  - TestDataQualityChecker       outlier / critical / impossibility checks
  - TestNormalizationIntegration end-to-end extraction → normalized result

Async tests use an in-memory SQLite database (StaticPool so all sessions share
one connection) seeded with reference ranges by the ``db_session`` fixture.
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from db.models import Base, ReferenceRange
from services.normalization.unit_converter import UnitConverter
from services.normalization.reference_lookup import ReferenceRangeLookup
from services.normalization.data_quality import DataQualityChecker
from services.normalization.normalizer import DataNormalizer
from models.normalization_schemas import (
    BiomarkerStatus,
    ExtractedBiomarker,
    NormalizationResult,
    NormalizedBiomarker as NormalizedBiomarkerSchema,
    PatientMetadata,
)

# code -> display name (for building API schemas in integration tests)
_DISPLAY = {"HGB": "Hemoglobin", "WBC": "WBC", "PLT": "Platelets", "RBC": "RBC"}


def _seed_ranges():
    """Reference ranges covering every fallback tier used by the tests."""
    return [
        # Hemoglobin — adult female (generic) and male
        ReferenceRange(biomarker_id="HGB", gender="F", age_min=18, age_max=65, lab_source="Any",
                       reference_min=12.0, reference_max=16.0, unit="g/dL",
                       source_guideline="WHO Hematology 2022"),
        ReferenceRange(biomarker_id="HGB", gender="M", age_min=18, age_max=65, lab_source="Any",
                       reference_min=13.5, reference_max=17.5, unit="g/dL",
                       source_guideline="WHO Hematology 2022"),
        # Hemoglobin — elderly male
        ReferenceRange(biomarker_id="HGB", gender="M", age_min=65, age_max=150, lab_source="Any",
                       reference_min=13.0, reference_max=17.0, unit="g/dL",
                       source_guideline="WHO Hematology 2022"),
        # Hemoglobin — pediatric (unisex)
        ReferenceRange(biomarker_id="HGB", gender=None, age_min=0, age_max=18, lab_source="Any",
                       reference_min=11.0, reference_max=14.0, unit="g/dL",
                       source_guideline="WHO Hematology 2022"),
        # Hemoglobin — lab-specific (Quest) adult female
        ReferenceRange(biomarker_id="HGB", gender="F", age_min=18, age_max=65, lab_source="Quest",
                       reference_min=11.5, reference_max=15.5, unit="g/dL",
                       source_guideline="Quest Hematology"),
        # Hemoglobin — pregnancy condition
        ReferenceRange(biomarker_id="HGB", gender="F", age_min=18, age_max=65, lab_source="Any",
                       condition="pregnancy", reference_min=11.0, reference_max=14.0, unit="g/dL",
                       source_guideline="WHO Pregnancy 2022"),
        # WBC — unisex adult
        ReferenceRange(biomarker_id="WBC", gender=None, age_min=18, age_max=65, lab_source="Any",
                       reference_min=4.0, reference_max=11.0, unit="K/uL",
                       source_guideline="WHO Hematology 2022"),
        # Platelets — unisex adult
        ReferenceRange(biomarker_id="PLT", gender=None, age_min=18, age_max=65, lab_source="Any",
                       reference_min=150.0, reference_max=400.0, unit="K/uL",
                       source_guideline="WHO Hematology 2022"),
    ]


# ── Fixtures ────────────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def db_session():
    """A seeded in-memory async DB session (shared connection via StaticPool)."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as seed:
        seed.add_all(_seed_ranges())
        await seed.commit()
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


@pytest.fixture
def unit_converter():
    """The (static) unit-conversion service."""
    return UnitConverter


@pytest.fixture
def reference_lookup(db_session):
    """Reference-range lookup bound to the seeded session."""
    return ReferenceRangeLookup(db_session)


@pytest.fixture
def normalizer(db_session, reference_lookup):
    """Fully-wired DataNormalizer."""
    return DataNormalizer(db_session, UnitConverter, reference_lookup)


@pytest.fixture
def sample_extracted_biomarkers():
    """A representative batch of extracted biomarkers (mixed statuses)."""
    return [
        {"name": "hemoglobin", "value": "13.5", "unit": "g/dL", "confidence": 0.96},
        {"name": "wbc", "value": "7.5", "unit": "K/uL", "confidence": 0.90},
        {"name": "platelets", "value": "450", "unit": "K/uL", "confidence": 0.80},
    ]


@pytest.fixture
def sample_patient_metadata():
    """Adult female patient, generic lab."""
    return {"gender": "F", "age": 45, "lab_source": "Any"}


# ── TestUnitConverter ───────────────────────────────────────────────────────
class TestUnitConverter:
    def test_hemoglobin_conversion_g_dl_to_g_l(self, unit_converter):
        """g/dL → g/L multiplies by 10 (10 g/dL == 100 g/L)."""
        assert unit_converter.convert("hemoglobin", 10.0, "g/dL", "g/L") == (100.0, "g/L")

    def test_hemoglobin_conversion_g_l_to_g_dl(self, unit_converter):
        """g/L → g/dL multiplies by 0.1 (100 g/L == 10 g/dL)."""
        assert unit_converter.convert("hemoglobin", 100.0, "g/L", "g/dL") == (10.0, "g/dL")

    def test_wbc_unit_equivalence(self, unit_converter):
        """K/uL, 10^3/uL and 10^9/L are numerically equivalent for WBC."""
        assert unit_converter.convert("wbc", 7.5, "10^9/L", "K/uL") == (7.5, "K/uL")
        assert unit_converter.convert("wbc", 7.5, "K/uL") == (7.5, "K/uL")  # default → standard

    def test_mcv_conversion(self, unit_converter):
        """fL and um^3 are equivalent for MCV."""
        assert unit_converter.convert("mcv", 90.0, "fL", "um^3") == (90.0, "um^3")

    def test_invalid_biomarker_raises_error(self, unit_converter):
        """Unknown biomarker → ValueError mentioning the biomarker."""
        with pytest.raises(ValueError, match="Unknown biomarker"):
            unit_converter.convert("glucose", 5.0, "mg/dL")

    def test_invalid_unit_raises_error(self, unit_converter):
        """Unknown unit → ValueError mentioning the unit."""
        with pytest.raises(ValueError, match="Unknown unit"):
            unit_converter.convert("hemoglobin", 10.0, "oz/gal")


# ── TestReferenceRangeLookup ────────────────────────────────────────────────
class TestReferenceRangeLookup:
    async def test_adult_female_hemoglobin_exact_match(self, reference_lookup):
        """Adult female HGB returns the 12–16 g/dL generic range."""
        r = await reference_lookup.get_reference_range("HGB", "F", 30, "Any")
        assert (r["ref_min"], r["ref_max"], r["unit"]) == (12.0, 16.0, "g/dL")
        assert r["gender"] == "F" and r["age_range"] == "18-65"

    async def test_elderly_male_hemoglobin(self, reference_lookup):
        """Age 70 buckets into 65–150 → elderly-male HGB range."""
        r = await reference_lookup.get_reference_range("HGB", "M", 70, "Any")
        assert (r["ref_min"], r["ref_max"]) == (13.0, 17.0)
        assert r["age_range"] == "65-150"

    async def test_pediatric_hemoglobin(self, reference_lookup):
        """Age 10 buckets into 0–18 → unisex pediatric range (gender fallback)."""
        r = await reference_lookup.get_reference_range("HGB", "F", 10, "Any")
        assert (r["ref_min"], r["ref_max"]) == (11.0, 14.0)
        assert r["age_range"] == "0-18" and r["gender"] is None

    async def test_unisex_wbc_fallback(self, reference_lookup):
        """Male WBC has no sex-specific row → falls back to the unisex range."""
        r = await reference_lookup.get_reference_range("WBC", "M", 30, "Any")
        assert (r["ref_min"], r["ref_max"]) == (4.0, 11.0)
        assert r["gender"] is None

    async def test_lab_specific_range(self, reference_lookup):
        """A Quest-specific range is preferred over the generic 'Any' range."""
        r = await reference_lookup.get_reference_range("HGB", "F", 30, "Quest")
        assert (r["ref_min"], r["ref_max"], r["lab_source"]) == (11.5, 15.5, "Quest")

    async def test_pregnancy_condition(self, reference_lookup):
        """A pregnancy-specific range is returned when the condition is supplied."""
        r = await reference_lookup.get_reference_range("HGB", "F", 30, "Any", "pregnancy")
        assert (r["ref_min"], r["ref_max"]) == (11.0, 14.0)
        assert r["source_guideline"] == "WHO Pregnancy 2022"

    async def test_missing_range_raises_error(self, reference_lookup):
        """A biomarker with no seeded range raises a clear ValueError."""
        with pytest.raises(ValueError, match="No reference range found"):
            await reference_lookup.get_reference_range("MCV", "M", 30)


# ── TestDataNormalizer ──────────────────────────────────────────────────────
class TestDataNormalizer:
    async def test_normalize_single_biomarker(self, normalizer, sample_patient_metadata):
        """A single in-range HGB normalizes to NORMAL with the canonical code."""
        out = await normalizer.normalize(
            [{"name": "hemoglobin", "value": "13.5", "unit": "g/dL", "confidence": 0.96}],
            sample_patient_metadata,
        )
        assert len(out) == 1
        assert out[0].biomarker_id == "HGB" and out[0].status == "NORMAL"
        assert out[0].loinc_code == "718-7"  # LOINC normalization (joins to the graph)

    async def test_normalize_multiple_biomarkers(
        self, normalizer, sample_extracted_biomarkers, sample_patient_metadata
    ):
        """All three biomarkers in the sample batch normalize successfully."""
        out = await normalizer.normalize(sample_extracted_biomarkers, sample_patient_metadata)
        assert {b.biomarker_id for b in out} == {"HGB", "WBC", "PLT"}

    async def test_unit_conversion_during_normalization(self, normalizer, sample_patient_metadata):
        """A g/L value is converted to the g/dL standard before comparison."""
        out = await normalizer.normalize(
            [{"name": "hemoglobin", "value": "130", "unit": "g/L", "confidence": 0.9}],
            sample_patient_metadata,
        )
        assert out[0].value == 13.0 and out[0].unit == "g/dL" and out[0].status == "NORMAL"

    async def test_unit_defaults_to_standard_when_missing(self, normalizer, sample_patient_metadata):
        """OCR often drops the unit cell — the value must still carry the standard unit
        (so Layer 4's g/dL↔g/L threshold check never compares an un-normalized value)."""
        out = await normalizer.normalize(
            [{"name": "hemoglobin", "value": "9.2", "unit": "", "confidence": 0.9}],
            sample_patient_metadata,
        )
        assert out[0].unit == "g/dL" and out[0].value == 9.2

    async def test_unit_case_insensitive_conversion(self, normalizer, sample_patient_metadata):
        """A lowercased 'g/l' unit still converts to the g/dL standard."""
        out = await normalizer.normalize(
            [{"name": "hemoglobin", "value": "92", "unit": "g/l", "confidence": 0.9}],
            sample_patient_metadata,
        )
        assert out[0].unit == "g/dL" and out[0].value == 9.2

    async def test_status_detection_low(self, normalizer, sample_patient_metadata):
        """Below reference_min → LOW."""
        out = await normalizer.normalize(
            [{"name": "hemoglobin", "value": "10.0", "unit": "g/dL", "confidence": 0.9}],
            sample_patient_metadata,
        )
        assert out[0].status == "LOW"

    async def test_status_detection_high(self, normalizer, sample_patient_metadata):
        """Above reference_max → HIGH."""
        out = await normalizer.normalize(
            [{"name": "hemoglobin", "value": "18.0", "unit": "g/dL", "confidence": 0.9}],
            sample_patient_metadata,
        )
        assert out[0].status == "HIGH"

    async def test_status_detection_normal(self, normalizer, sample_patient_metadata):
        """Within the interval → NORMAL."""
        out = await normalizer.normalize(
            [{"name": "hemoglobin", "value": "14.0", "unit": "g/dL", "confidence": 0.9}],
            sample_patient_metadata,
        )
        assert out[0].status == "NORMAL"

    async def test_partial_failure_continues_processing(self, normalizer, sample_patient_metadata):
        """An unknown biomarker is skipped + recorded; valid ones still return."""
        out = await normalizer.normalize(
            [
                {"name": "hemoglobin", "value": "13.5", "unit": "g/dL", "confidence": 0.9},
                {"name": "glucose", "value": "99", "unit": "mg/dL", "confidence": 0.5},
            ],
            sample_patient_metadata,
        )
        assert len(out) == 1 and out[0].biomarker_id == "HGB"
        assert len(normalizer.validation_issues) == 1
        assert "glucose" in normalizer.validation_issues[0]

    async def test_deviation_calculation(self, normalizer, sample_patient_metadata):
        """deviation_from_min and deviation_percent are computed off reference_min."""
        out = await normalizer.normalize(
            [{"name": "hemoglobin", "value": "13.5", "unit": "g/dL", "confidence": 0.9}],
            sample_patient_metadata,
        )
        assert out[0].deviation_from_min == 1.5          # 13.5 - 12.0
        assert out[0].deviation_percent == 12.5          # 1.5 / 12.0 * 100


# ── TestDataQualityChecker ──────────────────────────────────────────────────
class TestDataQualityChecker:
    def test_normal_value_passes(self):
        """An in-range value is valid with no issues."""
        r = DataQualityChecker.check_biomarker("hemoglobin", 13.5, "g/dL")
        assert r == {"valid": True, "quality_issues": [], "critical_flag": False, "reason": None}

    def test_critical_low_hemoglobin(self):
        """A critically-low but possible value is valid yet critical."""
        r = DataQualityChecker.check_biomarker("hemoglobin", 2.5, "g/dL")
        assert r["valid"] is True and r["critical_flag"] is True
        assert "critically low" in r["reason"]

    def test_critical_high_hemoglobin(self):
        """A critically-high but possible value is valid yet critical."""
        r = DataQualityChecker.check_biomarker("hemoglobin", 21.0, "g/dL")
        assert r["valid"] is True and r["critical_flag"] is True
        assert "critically high" in r["reason"]

    def test_absolute_limit_violation(self):
        """Below the absolute minimum → impossible (invalid) + critical."""
        r = DataQualityChecker.check_biomarker("hemoglobin", 0.2, "g/dL")
        assert r["valid"] is False and r["critical_flag"] is True
        assert r["reason"] == "Hemoglobin 0.2 is below absolute minimum 0.5"

    def test_multiple_quality_issues(self):
        """Several bad biomarkers each surface an issue (collected across checks)."""
        issues = [
            DataQualityChecker.check_biomarker("hemoglobin", 0.2, "g/dL")["reason"],
            DataQualityChecker.check_biomarker("wbc", 120.0, "K/uL")["reason"],
            DataQualityChecker.check_biomarker("platelets", 1600, "K/uL")["reason"],
        ]
        assert all(issues) and len(issues) == 3
        assert "absolute maximum" in issues[1] and "critically high" in issues[2]

    def test_is_physiologically_impossible_helper(self):
        """_is_physiologically_impossible flags out-of-absolute-range values."""
        assert DataQualityChecker._is_physiologically_impossible("hemoglobin", 0.2) is True
        assert DataQualityChecker._is_physiologically_impossible("hemoglobin", 13.5) is False
        assert DataQualityChecker._is_physiologically_impossible("rbc", 5.0) is False  # unknown → False

    def test_is_critical_helper(self):
        """_is_critical flags panic-threshold breaches (and ignores unknown biomarkers)."""
        assert DataQualityChecker._is_critical("wbc", 0.3) is True
        assert DataQualityChecker._is_critical("wbc", 7.0) is False
        assert DataQualityChecker._is_critical("rbc", 5.0) is False


# ── TestSchemas (Pydantic validation + error messages) ──────────────────────
class TestSchemas:
    def test_patient_metadata_normalizes_gender(self):
        """'female'/'male' normalize to 'F'/'M'; defaults apply."""
        pm = PatientMetadata(gender="female", age=30)
        assert pm.gender == "F" and pm.lab_source == "Any" and pm.condition is None

    def test_patient_metadata_rejects_invalid_gender(self):
        """An invalid gender raises a ValidationError."""
        with pytest.raises(ValidationError, match="gender must be"):
            PatientMetadata(gender="X", age=30)

    def test_extracted_biomarker_confidence_bounds(self):
        """Confidence outside [0, 1] is rejected."""
        with pytest.raises(ValidationError):
            ExtractedBiomarker(name="hgb", value="13", unit="g/dL", confidence=1.5)

    def test_normalized_biomarker_status_enum(self):
        """A NormalizedBiomarker coerces its status string to the enum."""
        nb = NormalizedBiomarkerSchema(
            biomarker_id="HGB", biomarker_name="Hemoglobin", value=13.5, unit="g/dL",
            reference_min=12.0, reference_max=16.0, gender="F", age_group="adult",
            lab_source="Any", status="NORMAL", deviation_from_min=1.5,
            deviation_percent=12.5, extraction_confidence=0.96,
        )
        assert nb.status is BiomarkerStatus.NORMAL and nb.quality_issues == []


# ── TestNormalizationIntegration ────────────────────────────────────────────
class TestNormalizationIntegration:
    @staticmethod
    def _to_result(norm: DataNormalizer, results, total_extracted: int) -> NormalizationResult:
        """Build the Pydantic NormalizationResult from normalizer output."""
        items = [
            NormalizedBiomarkerSchema(
                biomarker_id=r.biomarker_id,
                biomarker_name=_DISPLAY.get(r.biomarker_id, r.biomarker_id),
                value=r.value, unit=r.unit,
                reference_min=r.reference_min, reference_max=r.reference_max,
                gender=r.gender, age_group=r.age_group, lab_source=r.lab_source,
                status=BiomarkerStatus(r.status),
                deviation_from_min=r.deviation_from_min, deviation_percent=r.deviation_percent,
                extraction_confidence=r.extraction_confidence,
                quality_issues=r.quality_issues, critical_flag=r.critical_flag,
            )
            for r in results
        ]
        critical = [iss for r in results if r.critical_flag for iss in r.quality_issues]
        return NormalizationResult(
            status="success" if not norm.validation_issues else "partial",
            normalized_biomarkers=items,
            total_extracted=total_extracted,
            total_normalized=len(results),
            total_failed=len(norm.validation_issues),
            validation_issues=norm.validation_issues,
            critical_findings=critical,
            normalization_timestamp=datetime.now(timezone.utc).isoformat(),
            reference_ranges_version="1.0",
        )

    async def test_full_pipeline_from_extraction_to_normalized(
        self, normalizer, sample_extracted_biomarkers, sample_patient_metadata
    ):
        """Extraction batch → normalized → valid NormalizationResult schema."""
        results = await normalizer.normalize(sample_extracted_biomarkers, sample_patient_metadata)
        result = self._to_result(normalizer, results, len(sample_extracted_biomarkers))
        assert result.status == "success"
        assert result.total_extracted == 3 and result.total_normalized == 3
        assert result.total_failed == 0
        assert all(isinstance(b.status, BiomarkerStatus) for b in result.normalized_biomarkers)

    async def test_with_unit_conversion_and_quality_checks(self, normalizer, sample_patient_metadata):
        """A g/L Hb that converts to a critically-low g/dL is flagged critical."""
        batch = [{"name": "hemoglobin", "value": "10.0", "unit": "g/L", "confidence": 0.7}]
        results = await normalizer.normalize(batch, sample_patient_metadata)
        result = self._to_result(normalizer, results, len(batch))
        b = result.normalized_biomarkers[0]
        assert b.value == 1.0 and b.unit == "g/dL"        # 10 g/L → 1.0 g/dL
        assert b.status == BiomarkerStatus.LOW and b.critical_flag is True
        assert result.critical_findings  # populated

    async def test_with_multiple_biomarkers_mixed_status(self, normalizer, sample_patient_metadata):
        """A batch with LOW / NORMAL / HIGH yields each status correctly."""
        batch = [
            {"name": "hemoglobin", "value": "10.0", "unit": "g/dL", "confidence": 0.9},  # LOW
            {"name": "wbc", "value": "7.5", "unit": "K/uL", "confidence": 0.9},          # NORMAL
            {"name": "platelets", "value": "450", "unit": "K/uL", "confidence": 0.9},    # HIGH
        ]
        results = await normalizer.normalize(batch, sample_patient_metadata)
        statuses = {r.biomarker_id: r.status for r in results}
        assert statuses == {"HGB": "LOW", "WBC": "NORMAL", "PLT": "HIGH"}
