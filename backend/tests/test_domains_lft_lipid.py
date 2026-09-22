"""
Tests for the LFT and Lipid panels and the multi-panel pipeline wiring.

The CBC suites lock in CBC's tables; this module locks in the two new panels and
— more importantly — the *cross-panel* contracts that only break once a second
panel exists:

  - TestPanelRegistration      both panels registered and internally consistent
  - TestMergedTables           the registry unions panels without collisions
  - TestFeatureIdConvention    binary ids match what Layer 3 actually builds
  - TestReferenceRangeSeeds    seed rows are shaped for the Layer-2 lookup
  - TestUnitConversion         panel-specific units convert correctly
  - TestDataQuality            panel-specific panic values fire
  - TestPanelDetection         codes → panel, including mixed reports
  - TestSeverityBands          severity grading works upward and downward
  - TestSeeding                every panel lands in the DB, idempotently
  - TestEndToEndNormalization  an LFT / lipid panel survives Layers 2→3
  - TestOrchestratorPanelGating the required-biomarker check follows the panel
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from db.models import Base, ReferenceRange
from db.seeds import seed_all, seed_counts
from domains.base import check_domain
from domains.lft import DOMAIN as LFT_DOMAIN
from domains.lipid import DOMAIN as LIPID_DOMAIN
from domains.registry import (
    all_domains,
    available_domains,
    detect_panels,
    domain_for_code,
    get_domain,
    merged_biomarker_lookup,
    merged_code_to_loinc,
    merged_code_to_name,
    merged_feature_registry,
    merged_name_to_code,
    merged_unit_rules,
    merged_validation_rules,
)
from orchestration.cbc_orchestrator import CBCOrchestrator
from orchestration.layer1_adapter import Layer1ToLayer2Adapter
from services.feature_generation.feature_generator import FeatureGenerator
from services.feature_generation.severity_classifier import SeverityClassifier
from services.normalization.data_quality import DataQualityChecker
from services.normalization.normalizer import DataNormalizer
from services.normalization.reference_lookup import ReferenceRangeLookup
from services.normalization.unit_converter import UnitConverter

# The age bands ReferenceRangeLookup._get_age_category resolves to.
_LOOKUP_AGE_BANDS = {(0, 18), (18, 65), (65, 150)}


# ── Fixtures ────────────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def seeded_session():
    """An in-memory async session seeded with EVERY registered panel's ranges."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as seed:
        for domain in all_domains():
            seed.add_all(ReferenceRange(**row) for row in domain.reference_range_rows())
        await seed.commit()
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


@pytest.fixture
def normalizer_for(seeded_session):
    """A DataNormalizer bound to the seeded multi-panel database."""
    return DataNormalizer(seeded_session, UnitConverter, ReferenceRangeLookup(seeded_session))


# ── Registration ────────────────────────────────────────────────────────────
class TestPanelRegistration:
    @pytest.mark.parametrize("key", ["lft", "lipid"])
    def test_panel_is_registered(self, key):
        assert key in available_domains()
        assert get_domain(key).key == key

    def test_lft_is_consistent(self):
        assert check_domain(LFT_DOMAIN) == []

    def test_lipid_is_consistent(self):
        assert check_domain(LIPID_DOMAIN) == []

    def test_required_panels_are_the_clinical_minimum(self):
        assert set(LFT_DOMAIN.required_biomarkers) == {"ALT", "AST", "ALP", "TBIL", "ALB"}
        assert set(LIPID_DOMAIN.required_biomarkers) == {"CHOL", "LDL", "HDL", "TRIG"}

    @pytest.mark.parametrize("domain", [LFT_DOMAIN, LIPID_DOMAIN])
    def test_every_code_has_a_loinc_code(self, domain):
        """LOINC is Layer 4's join key to the graph — no code may be missing one."""
        assert set(domain.code_to_loinc) == set(domain.code_to_name)

    @pytest.mark.parametrize("domain", [LFT_DOMAIN, LIPID_DOMAIN])
    def test_every_code_has_graph_names(self, domain):
        assert set(domain.code_to_graph_names) == set(domain.code_to_name)


# ── Merged tables ───────────────────────────────────────────────────────────
class TestMergedTables:
    def test_merges_do_not_collide(self):
        """Every merged view builds without raising a cross-panel conflict."""
        for merge in (
            merged_name_to_code, merged_code_to_name, merged_code_to_loinc,
            merged_biomarker_lookup, merged_feature_registry, merged_unit_rules,
            merged_validation_rules,
        ):
            assert merge()

    def test_canonical_names_are_unique_across_panels(self):
        """Names become feature-id prefixes and unit-table keys — collisions would
        make one panel's feature fire on another panel's biomarker."""
        names = [name for d in all_domains() for name in d.code_to_name.values()]
        assert len(names) == len(set(names))

    def test_codes_are_unique_across_panels(self):
        codes = [code for d in all_domains() for code in d.code_to_name]
        assert len(codes) == len(set(codes))

    @pytest.mark.parametrize("code", ["ALT", "TBIL", "ALB", "CHOL", "LDL", "HDL", "TRIG"])
    def test_new_codes_reach_the_normalizer(self, code):
        assert code in merged_code_to_name()
        assert code in merged_code_to_loinc()

    @pytest.mark.parametrize(
        "alias,code",
        [("sgpt", "ALT"), ("sgot", "AST"), ("alkaline phosphatase", "ALP"),
         ("serum albumin", "ALB"), ("triglycerides", "TRIG"), ("hdl", "HDL")],
    )
    def test_ocr_aliases_reach_layer1(self, alias, code):
        assert merged_biomarker_lookup()[alias] == code

    @pytest.mark.parametrize(
        "line",
        ["Random Plasma Glucose:\tPlasma", "Random Plasma Glucose", "plasma glucose"],
    )
    def test_glucose_lines_do_not_resolve_to_the_ag_ratio(self, line):
        """Regression: a bare "a g" alias substring-matched inside "plasma glucose",
        so every glucose line on a real report became an A/G ratio of 80-odd —
        which then tripped the absolute-limit check as a critical value."""
        from orchestration.layer1_adapter import _resolve_biomarker_code

        assert _resolve_biomarker_code(line) is None

    @pytest.mark.parametrize(
        "line,code",
        [("A/G Ratio", "AGR"), ("Protein A/G Ratio", "AGR"), ("AG Ratio", "AGR"),
         ("Albumin Globulin Ratio", "AGR")],
    )
    def test_real_ag_ratio_labels_still_resolve(self, line, code):
        from orchestration.layer1_adapter import _resolve_biomarker_code

        assert _resolve_biomarker_code(line) == code

    def test_urgency_flags_stay_panel_specific(self):
        """Escalation differs by panel; the merge must keep both, not pick one."""
        by_domain = merged_validation_rules()["urgency_flags_by_domain"]
        assert by_domain["cbc"]["critical"]["escalation"] == "HEMATOLOGY_CONSULT"
        assert by_domain["lft"]["critical"]["escalation"] == "HEPATOLOGY_CONSULT"
        assert by_domain["lipid"]["critical"]["escalation"] == "LIPID_CLINIC_CONSULT"

    def test_a_cross_panel_alias_clash_is_rejected(self):
        """Two panels claiming one alias would silently misread a report; the
        registry must refuse it at registration, not pick a winner."""
        import dataclasses

        import domains.registry as registry
        from domains.base import DomainConsistencyError

        original = registry._REGISTRY["lft"]
        clashing = dataclasses.replace(
            original,
            biomarker_lookup={**original.biomarker_lookup, "hb": "ALT"},  # "hb" is CBC's
        )
        registry._REGISTRY["lft"] = clashing
        try:
            with pytest.raises(DomainConsistencyError, match="hb"):
                registry._validate_cross_panel_tables()
        finally:
            registry._REGISTRY["lft"] = original
        registry._validate_cross_panel_tables()   # restored state is clean again

    def test_severity_thresholds_cover_the_new_markers(self):
        thresholds = merged_validation_rules()["severity_thresholds"]
        for name in ("alt", "ast", "total_bilirubin", "albumin",
                     "ldl_cholesterol", "triglycerides", "hdl_cholesterol"):
            assert name in thresholds, f"{name} has no Layer-5 severity bands"


# ── Feature-id convention ───────────────────────────────────────────────────
class TestFeatureIdConvention:
    """Layer 3 builds binary ids as ``<code_to_name[code]>_<low|high>``; a
    definition whose id does not match that can never fire."""

    @pytest.mark.parametrize("domain", [LFT_DOMAIN, LIPID_DOMAIN])
    def test_binary_ids_match_generated_ids(self, domain):
        for fid, fd in domain.feature_registry.items():
            if fd.feature_type != "BINARY":
                continue
            base = domain.code_to_name[fd.biomarker_id]
            assert fid in (f"{base}_low", f"{base}_high"), (
                f"{domain.key}: binary feature {fid!r} would never be generated"
            )

    @pytest.mark.parametrize("domain", [LFT_DOMAIN, LIPID_DOMAIN])
    def test_ratio_operands_are_known_names(self, domain):
        """Ratio formulas are ``<name> / <name>`` or a single ``<name>`` over canonical
        names, where ``<name>_xuln`` is that marker as a multiple of its upper limit."""
        known = set(domain.code_to_name.values())
        known |= {f"{name}_xuln" for name in known}
        for fid, fd in domain.feature_registry.items():
            if fd.feature_type != "RATIO":
                continue
            operands = {p.strip() for p in fd.calculation_method.split("/", 1)}
            assert operands <= known, f"{fid} references an unknown marker"

    @pytest.mark.parametrize("domain", [LFT_DOMAIN, LIPID_DOMAIN])
    def test_severity_bands_are_ordered_and_contiguous(self, domain):
        for fid, fd in domain.feature_registry.items():
            if fd.feature_type != "SEVERITY":
                continue
            bands = sorted(fd.threshold_ranges.values())
            for (_, prev_high), (next_low, _) in zip(bands, bands[1:]):
                assert prev_high == next_low, f"{fid} has a gap/overlap at {prev_high}"

    @pytest.mark.parametrize("domain", [LFT_DOMAIN, LIPID_DOMAIN])
    def test_pattern_compositions_reference_real_features(self, domain):
        for fid, fd in domain.feature_registry.items():
            if fd.feature_type != "PATTERN":
                continue
            unknown = set(fd.feature_composition or []) - set(domain.feature_registry)
            assert not unknown, f"{fid} composes unknown feature(s) {sorted(unknown)}"


# ── Reference-range seed rows ───────────────────────────────────────────────
class TestReferenceRangeSeeds:
    @pytest.mark.parametrize("domain", [LFT_DOMAIN, LIPID_DOMAIN])
    def test_age_bands_match_the_lookup_buckets(self, domain):
        """A row on a band the lookup never queries is dead data."""
        for row in domain.reference_range_rows():
            assert (row["age_min"], row["age_max"]) in _LOOKUP_AGE_BANDS

    @pytest.mark.parametrize("domain", [LFT_DOMAIN, LIPID_DOMAIN])
    def test_rows_are_serum(self, domain):
        assert {row["specimen_type"] for row in domain.reference_range_rows()} == {"Serum"}

    @pytest.mark.parametrize("domain", [LFT_DOMAIN, LIPID_DOMAIN])
    def test_dimension_keys_are_unique(self, domain):
        """Duplicate dimensions would collide on the table's unique constraint."""
        keys = [
            (r["biomarker_id"], r["gender"], r["age_min"], r["age_max"],
             r["lab_source"], r["condition"])
            for r in domain.reference_range_rows()
        ]
        assert len(keys) == len(set(keys))

    @pytest.mark.parametrize("domain", [LFT_DOMAIN, LIPID_DOMAIN])
    def test_bounds_are_ordered(self, domain):
        for row in domain.reference_range_rows():
            low, high = row["reference_min"], row["reference_max"]
            assert low is not None or high is not None, f"{row['biomarker_id']} has no bounds"
            if low is not None and high is not None:
                assert low <= high

    @pytest.mark.parametrize("code", ["ALT", "AST", "ALP", "TBIL", "ALB"])
    def test_every_required_lft_marker_is_covered_for_adults(self, code):
        rows = [r for r in LFT_DOMAIN.reference_range_rows()
                if r["biomarker_id"] == code and (r["age_min"], r["age_max"]) == (18, 65)]
        assert rows, f"{code} has no adult range"

    def test_lipid_rows_are_one_sided_by_design(self):
        """Atherogenic markers are capped only; HDL is floored only."""
        rows = LIPID_DOMAIN.reference_range_rows()
        hdl = [r for r in rows if r["biomarker_id"] == "HDL"]
        assert hdl and all(r["reference_max"] is None for r in hdl)
        assert all(r["reference_min"] is not None for r in hdl)
        for code in ("CHOL", "LDL", "TRIG"):
            capped = [r for r in rows if r["biomarker_id"] == code]
            assert capped and all(r["reference_min"] is None for r in capped)
            assert all(r["reference_max"] is not None for r in capped)

    def test_lft_has_pregnancy_rows(self):
        """Placental ALP and haemodilution must not read as liver disease."""
        pregnancy = {
            r["biomarker_id"]: r for r in LFT_DOMAIN.reference_range_rows()
            if r["condition"] == "pregnancy"
        }
        assert set(pregnancy) == {"ALP", "ALB"}
        assert pregnancy["ALP"]["reference_max"] > 129.0     # wider than non-pregnant
        assert pregnancy["ALB"]["reference_min"] < 3.5

    def test_hdl_thresholds_are_sex_specific(self):
        adult = {
            r["gender"]: r["reference_min"]
            for r in LIPID_DOMAIN.reference_range_rows()
            if r["biomarker_id"] == "HDL" and (r["age_min"], r["age_max"]) == (18, 65)
        }
        assert adult["M"] == 40.0 and adult["F"] == 50.0


# ── Units ───────────────────────────────────────────────────────────────────
class TestUnitConversion:
    def test_enzyme_units_are_equivalent(self):
        assert UnitConverter.convert("alt", 55.0, "IU/L") == (55.0, "U/L")

    def test_bilirubin_umol_to_mg_dl(self):
        value, unit = UnitConverter.convert("total_bilirubin", 17.1, "umol/L", "mg/dL")
        assert unit == "mg/dL" and value == pytest.approx(1.0, abs=0.01)

    def test_albumin_g_l_to_g_dl(self):
        assert UnitConverter.convert("albumin", 40.0, "g/L", "g/dL") == (4.0, "g/dL")

    def test_cholesterol_mmol_to_mg_dl(self):
        value, _ = UnitConverter.convert("total_cholesterol", 5.0, "mmol/L")
        assert value == pytest.approx(193.35, abs=0.01)

    def test_triglycerides_use_their_own_factor(self):
        """TG and cholesterol have different molar masses — sharing a factor
        would misread a mmol/L report by ~2.3×."""
        tg, _ = UnitConverter.convert("triglycerides", 2.0, "mmol/L")
        chol, _ = UnitConverter.convert("total_cholesterol", 2.0, "mmol/L")
        assert tg == pytest.approx(177.14, abs=0.01)
        assert tg != chol

    def test_cbc_conversions_still_work(self):
        assert UnitConverter.convert("hemoglobin", 100.0, "g/L", "g/dL") == (10.0, "g/dL")

    @pytest.mark.parametrize("domain", [LFT_DOMAIN, LIPID_DOMAIN])
    def test_every_marker_has_a_standard_unit(self, domain):
        standard = merged_unit_rules()["standard_units"]
        for name in domain.code_to_name.values():
            assert name in standard, f"{name} has no standard unit"


# ── Data quality ────────────────────────────────────────────────────────────
class TestDataQuality:
    def test_alt_panic_value(self):
        result = DataQualityChecker.check_biomarker("alt", 1500.0, "U/L")
        assert result["valid"] is True and result["critical_flag"] is True

    def test_triglycerides_pancreatitis_threshold(self):
        result = DataQualityChecker.check_biomarker("triglycerides", 1200.0, "mg/dL")
        assert result["critical_flag"] is True
        assert "critically high" in result["reason"]

    def test_albumin_panic_low(self):
        result = DataQualityChecker.check_biomarker("albumin", 1.2, "g/dL")
        assert result["critical_flag"] is True

    def test_impossible_value_is_invalid(self):
        result = DataQualityChecker.check_biomarker("albumin", 90.0, "g/dL")
        assert result["valid"] is False and result["critical_flag"] is True

    def test_normal_values_pass(self):
        for name, value, unit in [("alt", 22.0, "U/L"), ("ldl_cholesterol", 90.0, "mg/dL")]:
            result = DataQualityChecker.check_biomarker(name, value, unit)
            assert result["valid"] is True and result["critical_flag"] is False


# ── Panel detection ─────────────────────────────────────────────────────────
class TestPanelDetection:
    def test_pure_lft_panel(self):
        detection = detect_panels(["ALT", "AST", "ALP", "TBIL", "ALB"])
        assert detection.primary == ["lft"] and not detection.unknown

    def test_pure_lipid_panel(self):
        detection = detect_panels(["CHOL", "LDL", "HDL", "TRIG"])
        assert detection.primary == ["lipid"]

    def test_mixed_report_picks_the_dominant_panel(self):
        detection = detect_panels(["HGB", "MCV", "ALT", "AST", "ALP", "TBIL", "ALB"])
        assert detection.primary == ["lft"]
        assert set(detection.matched) == {"cbc", "lft"}

    def test_unknown_codes_are_reported_not_matched(self):
        detection = detect_panels(["ALT", "AST", "ALP", "TBIL", "ALB", "XYZ"])
        assert detection.unknown == ["XYZ"] and detection.primary == ["lft"]

    def test_detection_is_case_insensitive(self):
        assert detect_panels(["alt", "ast"]).primary == ["lft"]

    @pytest.mark.parametrize(
        "code,expected", [("HGB", "cbc"), ("ALT", "lft"), ("TRIG", "lipid"), ("NOPE", None)]
    )
    def test_domain_for_code(self, code, expected):
        assert domain_for_code(code) == expected


# ── Severity ────────────────────────────────────────────────────────────────
class TestSeverityBands:
    @pytest.mark.parametrize(
        "code,value,expected",
        [("ALT", 20.0, "normal"), ("ALT", 300.0, "severe"), ("ALT", 1500.0, "critical"),
         ("TBIL", 0.8, "normal"), ("TBIL", 4.0, "moderate"),
         ("ALB", 2.2, "severe"), ("ALB", 4.2, "normal"),
         ("TRIG", 120.0, "normal"), ("TRIG", 1500.0, "critical"),
         ("LDL", 200.0, "critical"), ("HDL", 25.0, "severe"), ("HDL", 60.0, "normal")],
    )
    def test_classification(self, code, value, expected):
        assert SeverityClassifier.classify_severity(code, value) == expected

    def test_cbc_severity_is_unaffected(self):
        assert SeverityClassifier.classify_severity("HGB", 6.0) == "severe"


# ── Seeding ─────────────────────────────────────────────────────────────────
class TestSeeding:
    @pytest.fixture
    def sync_session(self):
        """A synchronous in-memory session (``seed_all`` is the sync seeder)."""
        from sqlalchemy import create_engine

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            yield session
        engine.dispose()

    def test_seed_all_loads_every_panel(self, sync_session):
        inserted = seed_all(sync_session)
        counts = seed_counts()
        assert inserted == sum(counts.values())
        assert set(counts) == set(available_domains())

        stored = sync_session.execute(
            select(ReferenceRange.biomarker_id, func.count())
            .group_by(ReferenceRange.biomarker_id)
        ).all()
        codes = {code for code, _ in stored}
        assert {"HGB", "ALT", "TBIL", "CHOL", "HDL"} <= codes

    def test_seeding_is_idempotent(self, sync_session):
        first = seed_all(sync_session)
        total_after_first = sync_session.execute(
            select(func.count()).select_from(ReferenceRange)
        ).scalar_one()
        second = seed_all(sync_session)
        total_after_second = sync_session.execute(
            select(func.count()).select_from(ReferenceRange)
        ).scalar_one()

        assert first > 0 and second == 0
        assert total_after_first == total_after_second

    def test_specimen_types_coexist(self, sync_session):
        seed_all(sync_session)
        specimens = set(
            sync_session.execute(select(ReferenceRange.specimen_type).distinct()).scalars()
        )
        assert {"Whole Blood", "Serum"} <= specimens


# ── End-to-end (Layers 2→3) ─────────────────────────────────────────────────
@pytest.mark.asyncio
class TestEndToEndNormalization:
    async def test_lft_panel_normalizes_and_generates_features(self, normalizer_for):
        extracted = [
            {"name": "SGPT", "value": "180", "unit": "U/L", "confidence": 0.95},
            {"name": "SGOT", "value": "95", "unit": "U/L", "confidence": 0.95},
            {"name": "alkaline phosphatase", "value": "210", "unit": "U/L", "confidence": 0.9},
            {"name": "total bilirubin", "value": "3.4", "unit": "mg/dL", "confidence": 0.9},
            {"name": "albumin", "value": "2.8", "unit": "g/dL", "confidence": 0.9},
        ]
        normalized = await normalizer_for.normalize(extracted, {"gender": "M", "age": 45})
        assert len(normalized) == 5 and not normalizer_for.validation_issues

        by_code = {nb.biomarker_id: nb for nb in normalized}
        assert by_code["ALT"].status == "HIGH"
        assert by_code["ALB"].status == "LOW"
        assert by_code["TBIL"].reference_max == 1.2
        assert by_code["ALT"].loinc_code == "1742-6"

        features = await FeatureGenerator(merged_feature_registry()).generate_features(normalized)
        ids = {f.feature_id for f in features}
        assert {"alt_high", "ast_high", "alp_high", "total_bilirubin_high",
                "albumin_low"} <= ids
        assert "de_ritis_ratio" in ids            # AST/ALT computed from both operands
        severities = {f.feature_id: f.value for f in features if f.feature_type == "SEVERITY"}
        assert severities["alt_severity"] == "moderate"
        assert severities["albumin_severity"] == "moderate"

    async def test_lipid_panel_normalizes_with_one_sided_ranges(self, normalizer_for):
        extracted = [
            {"name": "total cholesterol", "value": "245", "unit": "mg/dL", "confidence": 0.95},
            {"name": "ldl cholesterol", "value": "165", "unit": "mg/dL", "confidence": 0.95},
            {"name": "hdl cholesterol", "value": "34", "unit": "mg/dL", "confidence": 0.95},
            {"name": "triglycerides", "value": "260", "unit": "mg/dL", "confidence": 0.95},
        ]
        normalized = await normalizer_for.normalize(extracted, {"gender": "M", "age": 52})
        by_code = {nb.biomarker_id: nb for nb in normalized}

        assert by_code["CHOL"].status == "HIGH"
        assert by_code["HDL"].status == "LOW"            # floored range → LOW side only
        assert by_code["HDL"].reference_max is None
        assert by_code["TRIG"].reference_min is None     # capped range → HIGH side only

        features = await FeatureGenerator(merged_feature_registry()).generate_features(normalized)
        ids = {f.feature_id for f in features}
        assert {"total_cholesterol_high", "ldl_cholesterol_high",
                "hdl_cholesterol_low", "triglycerides_high"} <= ids
        assert {"chol_hdl_ratio", "ldl_hdl_ratio", "trig_hdl_ratio"} <= ids

    async def test_female_hdl_threshold_is_applied(self, normalizer_for):
        """The same HDL value is LOW for a woman and NORMAL for a man."""
        extracted = [{"name": "hdl", "value": "45", "unit": "mg/dL", "confidence": 0.9}]
        female = await normalizer_for.normalize(extracted, {"gender": "F", "age": 40})
        male = await normalizer_for.normalize(extracted, {"gender": "M", "age": 40})
        assert female[0].status == "LOW"
        assert male[0].status == "NORMAL"

    async def test_pregnancy_alp_uses_the_condition_row(self, normalizer_for):
        """An ALP of 200 U/L is HIGH for a non-pregnant woman, NORMAL in pregnancy."""
        extracted = [{"name": "alkaline phosphatase", "value": "200", "unit": "U/L",
                      "confidence": 0.9}]
        baseline = await normalizer_for.normalize(extracted, {"gender": "F", "age": 30})
        pregnant = await normalizer_for.normalize(
            extracted, {"gender": "F", "age": 30, "condition": "pregnancy"}
        )
        assert baseline[0].status == "HIGH"
        assert pregnant[0].status == "NORMAL"

    async def test_bilirubin_in_umol_is_converted_before_comparison(self, normalizer_for):
        extracted = [{"name": "total bilirubin", "value": "51.3", "unit": "umol/L",
                      "confidence": 0.9}]
        normalized = await normalizer_for.normalize(extracted, {"gender": "M", "age": 40})
        assert normalized[0].unit == "mg/dL"
        assert normalized[0].value == pytest.approx(3.0, abs=0.05)
        assert normalized[0].status == "HIGH"

    async def test_ocr_rows_resolve_across_panels(self):
        """A single OCR'd report may mix panels; Layer 1 resolves all of them."""
        rows = [
            {"name": "Haemoglobin", "value": 11.1, "unit": "g/dL", "confidence": 0.9},
            {"name": "S.G.P.T", "value": 88, "unit": "U/L", "confidence": 0.9},
            {"name": "Serum Albumin", "value": 3.1, "unit": "g/dL", "confidence": 0.9},
            {"name": "Total Cholesterol", "value": 230, "unit": "mg/dL", "confidence": 0.9},
            {"name": "HDL Cholesterol", "value": 38, "unit": "mg/dL", "confidence": 0.9},
        ]
        resolved, _ = Layer1ToLayer2Adapter.convert_ocr_rows_to_biomarker_dict(rows)
        assert resolved == {"HGB": 11.1, "ALT": 88.0, "ALB": 3.1, "CHOL": 230.0, "HDL": 38.0}


# ── Orchestrator panel gating ───────────────────────────────────────────────
@pytest.mark.asyncio
class TestOrchestratorPanelGating:
    """Layer 0 of the orchestrator: the required-biomarker check must follow the
    panel that was actually submitted, not always CBC's."""

    @pytest.fixture
    def orchestrator(self):
        return CBCOrchestrator(None, None, None, None)

    async def test_complete_lft_panel_passes(self, orchestrator):
        valid, errors = await orchestrator._validate_inputs(
            {"ALT": 180.0, "AST": 95.0, "ALP": 210.0, "TBIL": 3.4, "ALB": 2.8}
        )
        assert valid and errors == []

    async def test_complete_lipid_panel_passes(self, orchestrator):
        valid, errors = await orchestrator._validate_inputs(
            {"CHOL": 245.0, "LDL": 165.0, "HDL": 34.0, "TRIG": 260.0}
        )
        assert valid and errors == []

    async def test_incomplete_lft_panel_names_the_missing_liver_markers(self, orchestrator):
        valid, errors = await orchestrator._validate_inputs({"ALT": 180.0, "AST": 95.0})
        assert not valid
        assert "Liver Function Test" in errors[0]
        assert "ALP" in errors[0] and "TBIL" in errors[0] and "ALB" in errors[0]

    async def test_complete_cbc_panel_still_passes(self, orchestrator):
        valid, errors = await orchestrator._validate_inputs(
            {"HGB": 10.2, "MCV": 75.0, "RDW": 16.5, "WBC": 5.2, "PLT": 250.0}
        )
        assert valid and errors == []

    async def test_incomplete_cbc_panel_still_fails(self, orchestrator):
        valid, errors = await orchestrator._validate_inputs({"HGB": 10.2, "MCV": 75.0})
        assert not valid and "Complete Blood Count" in errors[0]

    async def test_unrecognised_codes_are_rejected(self, orchestrator):
        valid, errors = await orchestrator._validate_inputs({"FOO": 1.0, "BAR": 2.0})
        assert not valid and "No recognised biomarkers" in errors[0]

    async def test_non_numeric_value_is_rejected(self, orchestrator):
        valid, errors = await orchestrator._validate_inputs(
            {"ALT": 180.0, "AST": 95.0, "ALP": 210.0, "TBIL": 3.4, "ALB": "low"}
        )
        assert not valid and any("non-numeric" in e for e in errors)
