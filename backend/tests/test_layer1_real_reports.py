"""
Layer-1 regressions found by running real lab reports through the live pipeline.

Every case here reproduces a defect that a real Vijaya Diagnostic / CMR Hospital
report exposed, with the value that was actually misread:

  - TestNumberParsing        "0,4" was read as 4.0 (decimal comma stripped)
  - TestShortTokenResolution "MC-2657" (a registration stamp) became MCV = 2657
  - TestUnitPreservation     units were dropped between Layer 1 and Layer 2
  - TestRepeatedBiomarkers   a two-draw PDF was silently blended into one panel
  - TestIndianUnits          lakhs/cumm and Cells/cumm now convert correctly
  - TestOversizedPdfSplit    3.9MB reports were rejected wholesale by OCR.space
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from db.models import Base, ReferenceRange
from domains.registry import all_domains
from orchestration.cbc_orchestrator import CBCOrchestrator
from orchestration.layer1_adapter import Layer1ToLayer2Adapter, _resolve_biomarker_code
from orchestration.ocr_space_client import OCRSpaceClient, _parse_number
from services.normalization.normalizer import DataNormalizer
from services.normalization.reference_lookup import ReferenceRangeLookup
from services.normalization.unit_converter import UnitConverter


def _parse(text: str):
    """Run the real OCR text parser without constructing a client (no API key)."""
    return OCRSpaceClient._parse_text(OCRSpaceClient.__new__(OCRSpaceClient), text)


# ── Number parsing ──────────────────────────────────────────────────────────
class TestNumberParsing:
    @pytest.mark.parametrize(
        "token,expected",
        [("0,4", 0.4),        # decimal comma — CMR total bilirubin, was read as 4.0
         ("12,5", 12.5),
         ("1,25", 1.25),
         ("150,000", 150000.0),          # thousands
         ("2,88,000", 288000.0),         # Indian lakh grouping
         ("1,234.5", 1234.5),            # grouping + decimal point
         ("85.6", 85.6),
         ("288000", 288000.0)],
    )
    def test_comma_disambiguation(self, token, expected):
        assert _parse_number(token) == expected

    def test_bilirubin_line_is_not_multiplied_by_ten(self):
        """The exact CMR line: a 0.4 mg/dL bilirubin must not read as 4.0 (which
        flagged as HIGH with 'moderate' severity — a false jaundice)."""
        rows = _parse("Total Bilirubin\t0,4\t0.3 - 1.2 mg/di")
        assert rows[0]["value"] == 0.4

    def test_grouped_platelet_count_is_not_truncated(self):
        """The old regex captured only "2,88" out of "2,88,000" → 288."""
        rows = _parse("Platelet count 2,88,000 Cells/cumm")
        assert rows[0]["value"] == 288000.0


# ── Short-token resolution ──────────────────────────────────────────────────
class TestShortTokenResolution:
    @pytest.mark.parametrize(
        "token",
        ["MC", "MC-",      # Vijaya's "MC-2657" registration stamp → was MCV
         "PT",             # prothrombin time → was PLT
         "TR", "Tab", "Cap", "INR", "APTT", "ESR", "CRP", "TSH", "EF", "DM", "HTN"],
    )
    def test_short_non_biomarker_tokens_do_not_resolve(self, token):
        assert _resolve_biomarker_code(token) is None

    @pytest.mark.parametrize(
        "token,code",
        [("Hb", "HGB"), ("HGB", "HGB"), ("PLT", "PLT"), ("WBC", "WBC"), ("MCV", "MCV"),
         ("MCH", "MCH"), ("MCHC", "MCHC"), ("RDW", "RDW"), ("HCT", "HCT"), ("RBC", "RBC"),
         ("ALT", "ALT"), ("AST", "AST"), ("ALP", "ALP"), ("GGT", "GGT"), ("ALB", "ALB"),
         ("HDL", "HDL"), ("LDL", "LDL"), ("TG", "TRIG")],
    )
    def test_short_real_aliases_still_resolve_exactly(self, token, code):
        assert _resolve_biomarker_code(token) == code

    @pytest.mark.parametrize(
        "token,code",
        [("M.C.V", "MCV"), ("H b", "HGB"), ("R.D.W", "RDW"), ("S.G.P.T", "ALT"),
         ("Haemoglobln", "HGB"), ("Platlets", "PLT"), ("Triglycerldes", "TRIG"),
         ("Cholesterol", "CHOL")],
    )
    def test_despaced_and_typo_forms_still_resolve(self, token, code):
        """The length guard must not cost OCR-typo tolerance on longer names."""
        assert _resolve_biomarker_code(token) == code

    def test_registration_stamp_does_not_displace_the_real_mcv(self):
        """On the Vijaya report the stamp is on page 1 and the real MCV on page 4;
        first-occurrence-wins meant the stamp won and 85.6 was discarded."""
        rows = _parse("MC-2657\tRegistration No: TSMC/FMR/31118\nMCV\t: 85.6\tfL\t83.0 - 101.0")
        resolved, _ = Layer1ToLayer2Adapter.convert_ocr_rows_to_biomarker_dict(rows)
        assert resolved["MCV"] == 85.6


# ── Units survive the Layer 1 → Layer 2 hand-off ────────────────────────────
class TestUnitPreservation:
    def test_adapter_reports_the_units_it_read(self):
        rows = [{"name": "Platelet count", "value": 2.91, "unit": "lakhs/cumm", "confidence": 0.9},
                {"name": "WBC Count", "value": 8000, "unit": "Cells/cumm", "confidence": 0.9}]
        resolved, meta = Layer1ToLayer2Adapter.convert_ocr_rows_to_biomarker_dict(rows)
        assert resolved == {"PLT": 2.91, "WBC": 8000.0}
        assert meta["units"] == {"PLT": "lakhs/cumm", "WBC": "Cells/cumm"}

    def test_orchestrator_passes_units_to_layer2(self):
        """Regression: the orchestrator hardcoded ``"unit": ""``, so UnitConverter
        never ran on the file-upload path."""
        captured = {}

        class _SpyNormalizer:
            async def normalize(self, extracted, patient_metadata):
                captured["extracted"] = extracted
                raise RuntimeError("stop after capture")

        orch = CBCOrchestrator(_SpyNormalizer(), None, None, None)
        import asyncio

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            orch.analyze_cbc(
                {"HGB": 12.8, "MCV": 79.3, "RDW": 13.0, "WBC": 8000.0, "PLT": 2.91},
                "pt", metadata={"units": {"PLT": "lakhs/cumm", "WBC": "Cells/cumm"}},
                gender="F", age_years=45,
            )
        )
        units = {row["name"]: row["unit"] for row in captured["extracted"]}
        assert units["PLT"] == "lakhs/cumm"
        assert units["WBC"] == "Cells/cumm"


# ── Repeated biomarkers (multi-draw documents) ──────────────────────────────
class TestRepeatedBiomarkers:
    def test_second_draw_is_reported_not_silently_dropped(self):
        """A real 9-page CMR PDF held two CBCs (05-Jun and 12-Jun). The blend must
        at least be visible to the caller."""
        rows = [{"name": "Haemoglobin", "value": 12.8, "unit": "gm%", "confidence": 0.9},
                {"name": "WBC Count", "value": 6.71, "unit": "X1000cells/cumm", "confidence": 0.9},
                {"name": "Haemoglobin", "value": 12.3, "unit": "gm%", "confidence": 0.9},
                {"name": "WBC Count", "value": 6.49, "unit": "X1000cells/cumm", "confidence": 0.9}]
        resolved, meta = Layer1ToLayer2Adapter.convert_ocr_rows_to_biomarker_dict(rows)
        assert resolved == {"HGB": 12.8, "WBC": 6.71}          # first draw wins
        assert meta["repeated_biomarkers"] == {"HGB": [12.8, 12.3], "WBC": [6.71, 6.49]}

    def test_single_draw_reports_no_repeats(self):
        rows = [{"name": "Haemoglobin", "value": 12.8, "unit": "gm%", "confidence": 0.9},
                {"name": "MCV", "value": 79.3, "unit": "fl", "confidence": 0.9}]
        _, meta = Layer1ToLayer2Adapter.convert_ocr_rows_to_biomarker_dict(rows)
        assert meta["repeated_biomarkers"] == {}


# ── Indian laboratory units ─────────────────────────────────────────────────
class TestIndianUnits:
    @pytest.mark.parametrize(
        "name,value,unit,expected,std",
        [("platelets", 2.91, "lakhs/cumm", 291.0, "K/uL"),
         ("platelets", 1.21, "lakhs/cumm", 121.0, "K/uL"),
         ("platelets", 288000, "Cells/cumm", 288.0, "K/uL"),
         ("wbc", 8000, "Cells/cumm", 8.0, "K/uL"),
         ("wbc", 9.65, "X1000cells/cumm", 9.65, "K/uL"),
         ("rbc", 4.68, "Millions/cumm", 4.68, "M/uL"),
         ("hemoglobin", 12.8, "gm%", 12.8, "g/dL"),
         ("hematocrit", 37.1, "Vol%", 37.1, "%"),
         ("albumin", 3.9, "gm/dL", 3.9, "g/dL")],
    )
    def test_conversion(self, name, value, unit, expected, std):
        assert UnitConverter.convert(name, value, unit) == (expected, std)

    def test_matching_is_case_insensitive(self):
        assert UnitConverter.convert("platelets", 2.91, "LAKHS/CUMM")[0] == 291.0


# ── End-to-end: the false criticals are gone ────────────────────────────────
@pytest_asyncio.fixture
async def seeded_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                 connect_args={"check_same_thread": False},
                                 poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as seed:
        for domain in all_domains():
            seed.add_all(ReferenceRange(**row) for row in domain.reference_range_rows())
        await seed.commit()
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
class TestNoMoreFalseCriticals:
    async def test_platelets_in_lakhs_are_normal_not_critical(self, seeded_session):
        """CMR reported 2.91 lakhs/cumm = 291 K/uL. It was flagged CRITICAL."""
        norm = DataNormalizer(seeded_session, UnitConverter, ReferenceRangeLookup(seeded_session))
        out = await norm.normalize(
            [{"name": "PLT", "value": 2.91, "unit": "lakhs/cumm", "confidence": 0.9}],
            {"gender": "F", "age": 43},
        )
        assert out[0].value == 291.0
        assert out[0].status == "NORMAL"
        assert out[0].critical_flag is False

    async def test_wbc_in_cells_per_cumm_is_normal_not_critical(self, seeded_session):
        """Vijaya reported 8000 Cells/cumm = 8 K/uL. It breached the absolute max."""
        norm = DataNormalizer(seeded_session, UnitConverter, ReferenceRangeLookup(seeded_session))
        out = await norm.normalize(
            [{"name": "WBC", "value": 8000, "unit": "Cells/cumm", "confidence": 0.9}],
            {"gender": "M", "age": 18},
        )
        assert out[0].value == 8.0
        assert out[0].status == "NORMAL"
        assert out[0].critical_flag is False

    async def test_genuinely_low_platelets_are_still_low(self, seeded_session):
        """1.21 lakhs/cumm = 121 K/uL — really is below range, just not critical."""
        norm = DataNormalizer(seeded_session, UnitConverter, ReferenceRangeLookup(seeded_session))
        out = await norm.normalize(
            [{"name": "PLT", "value": 1.21, "unit": "lakhs/cumm", "confidence": 0.9}],
            {"gender": "F", "age": 45},
        )
        assert out[0].value == 121.0
        assert out[0].status == "LOW"
        assert out[0].critical_flag is False

    async def test_ocr_garbled_count_units_still_convert(self, seeded_session):
        """Live CMR run: "lakhs/cumnt.5" was unrecognised, so 1.21 lakhs/cumm (121 K/uL)
        was escalated as critical thrombocytopenia at 1.21 K/uL."""
        norm = DataNormalizer(seeded_session, UnitConverter, ReferenceRangeLookup(seeded_session))
        out = await norm.normalize(
            [{"name": "PLT", "value": 1.21, "unit": "lakhs/cumnt.5", "confidence": 0.9},
             {"name": "WBC", "value": 9.65, "unit": "X1000cells/curtr", "confidence": 0.9},
             {"name": "WBC", "value": 8000, "unit": "Cells/cmm.", "confidence": 0.9}],
            {"gender": "F", "age": 45},
        )
        plt, wbc_thousands, wbc_cells = out
        assert plt.value == 121.0 and plt.status == "LOW" and plt.critical_flag is False
        assert any("OCR-tolerant" in issue for issue in plt.quality_issues)
        assert wbc_thousands.value == 9.65
        assert wbc_cells.value == 8.0

    async def test_tolerant_matching_never_guesses_between_scales(self):
        # "cells/cumm" (x 0.001) and "X1000cells/cumm" (x 1) must stay distinct.
        factors = UnitConverter.CONVERSION_FACTORS["wbc"]
        assert factors[UnitConverter._resolve_unit("wbc", "cells/cumnt")] == 0.001
        assert factors[UnitConverter._resolve_unit("wbc", "X1000cells/cumnt")] == 1.0
        assert UnitConverter._resolve_unit("platelets", "squigs/cumm") is None

    async def test_unknown_unit_is_recorded_as_a_quality_issue(self, seeded_session):
        """An unrecognised unit is still assumed standard, but no longer silently."""
        norm = DataNormalizer(seeded_session, UnitConverter, ReferenceRangeLookup(seeded_session))
        out = await norm.normalize(
            [{"name": "PLT", "value": 250, "unit": "squigs/furlong", "confidence": 0.9}],
            {"gender": "F", "age": 45},
        )
        assert any("Unrecognized unit" in issue for issue in out[0].quality_issues)


# ── Oversized PDFs are split rather than rejected ───────────────────────────
class TestOversizedPdfSplit:
    """OCR.space caps uploads at 1.5 MB (free plan) and 413s anything larger,
    which the API surfaced as a 502. Real reports are 2-4 MB; their pages are not."""

    @staticmethod
    def _pdf(path, pages: int, filler: int = 0):
        from pypdf import PdfWriter

        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=612, height=792)
        if filler:                      # pad the file past the size threshold
            writer.add_metadata({"/Comment": "x" * filler})
        with open(path, "wb") as fh:
            writer.write(fh)
        return str(path)

    def test_small_pdf_is_not_split(self, tmp_path):
        from orchestration.ocr_space_client import _needs_page_split

        path = self._pdf(tmp_path / "small.pdf", pages=2)
        assert _needs_page_split(path, 1_200_000) is False

    def test_large_pdf_is_split(self, tmp_path):
        from orchestration.ocr_space_client import _needs_page_split

        path = self._pdf(tmp_path / "big.pdf", pages=3, filler=2_000_000)
        assert _needs_page_split(path, 1_200_000) is True

    def test_images_are_never_split(self, tmp_path):
        from orchestration.ocr_space_client import _needs_page_split

        img = tmp_path / "scan.png"
        img.write_bytes(b"\x89PNG" + b"0" * 2_000_000)
        assert _needs_page_split(str(img), 1_200_000) is False

    def test_split_yields_one_file_per_page(self, tmp_path):
        import os

        from orchestration.ocr_space_client import _split_pdf_pages

        path = self._pdf(tmp_path / "multi.pdf", pages=5)
        pages = _split_pdf_pages(path)
        try:
            assert len(pages) == 5
            assert all(os.path.getsize(p) > 0 for p in pages)
        finally:
            for p in pages:
                os.unlink(p)

    @pytest.mark.asyncio
    async def test_oversized_pdf_is_ocrd_page_by_page_in_order(self, tmp_path):
        """The concatenated text must match what one call would have returned."""
        from orchestration.ocr_space_client import OCRSpaceClient

        client = OCRSpaceClient.__new__(OCRSpaceClient)
        client.max_upload_bytes = 1_200_000
        seen = []

        async def fake_single(page_path):
            seen.append(page_path)
            return f"page {len(seen)} text"

        client._ocr_single = fake_single
        path = self._pdf(tmp_path / "report.pdf", pages=4, filler=2_000_000)
        text = await client._ocr_text(path)

        assert len(seen) == 4                       # one call per page, not one for the file
        assert text == "page 1 text\npage 2 text\npage 3 text\npage 4 text"

    @pytest.mark.asyncio
    async def test_one_bad_page_does_not_lose_the_report(self, tmp_path):
        from orchestration.ocr_space_client import OCRSpaceClient, OCRSpaceError

        client = OCRSpaceClient.__new__(OCRSpaceClient)
        client.max_upload_bytes = 1_200_000
        calls = {"n": 0}

        async def flaky(page_path):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OCRSpaceError("page 2 exploded")
            return f"page {calls['n']} text"

        client._ocr_single = flaky
        path = self._pdf(tmp_path / "report.pdf", pages=3, filler=2_000_000)
        text = await client._ocr_text(path)

        assert "page 1 text" in text and "page 3 text" in text
        assert "OCR failed on page 2" in text

    @pytest.mark.asyncio
    async def test_all_pages_failing_still_raises(self, tmp_path):
        from orchestration.ocr_space_client import OCRSpaceClient, OCRSpaceError

        client = OCRSpaceClient.__new__(OCRSpaceClient)
        client.max_upload_bytes = 1_200_000

        async def always_fail(page_path):
            raise OCRSpaceError("nope")

        client._ocr_single = always_fail
        path = self._pdf(tmp_path / "report.pdf", pages=2, filler=2_000_000)
        with pytest.raises(OCRSpaceError, match="all 2 pages"):
            await client._ocr_text(path)
