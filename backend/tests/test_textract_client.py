"""
Tests for the AWS Textract Layer-1 provider.

The fixtures below are Textract ``Blocks`` reconstructed from a **real**
``AnalyzeDocument`` response for page 2 of the CMR Hospital report — the page
whose wrapped MCHC label and drifting columns defeated the flat-text provider.
Building the blocks locally keeps the suite free of AWS calls (and their cost)
while exercising the real parsing path.
"""

import pytest

from orchestration.textract_client import (
    TextractClient,
    TextractError,
    _column_roles,
    _split_value_and_unit,
)


# ── Textract response builder ───────────────────────────────────────────────
def _blocks_for(rows, table_confidence=98.5):
    """Build a minimal but faithful Textract Blocks list for one table."""
    blocks = []
    cell_ids = []
    for r, row in enumerate(rows, start=1):
        for c, text in enumerate(row, start=1):
            word_ids = []
            for w, word in enumerate(str(text).split()):
                wid = f"w{r}_{c}_{w}"
                blocks.append({"Id": wid, "BlockType": "WORD", "Text": word})
                word_ids.append(wid)
            cid = f"cell{r}_{c}"
            cell = {"Id": cid, "BlockType": "CELL", "RowIndex": r, "ColumnIndex": c}
            if word_ids:
                cell["Relationships"] = [{"Type": "CHILD", "Ids": word_ids}]
            blocks.append(cell)
            cell_ids.append(cid)
    blocks.append({
        "Id": "table1", "BlockType": "TABLE", "Confidence": table_confidence,
        "Relationships": [{"Type": "CHILD", "Ids": cell_ids}],
    })
    return {"Blocks": blocks}


# The real CMR page-2 table, verbatim from the observed Textract response.
CMR_CBC_TABLE = [
    ["Parameter", "Specimen", "Results", "Biological Reference", "Method"],
    ["COMPLETE BLOOD PICTURE", "", "", "", ""],
    ["Haemoglobin", "Whole Blood", "12.0 gm%", "12.0-15.0 gm%", "SLS method"],
    ["RBC Count", "", "4.68", "Millions/cumm3.8-4.8 Millions/cumm", "Impedance"],
    ["WBC Count", "", "9.65 X1000cells/curar6", "- 11.0 X1000cells/cumm", "Impedance"],
    ["Platelet count", "", "1.21 lakhs/cummt.5", "- 4.0 lakhs/cumm", "Impedance/ Light Microscopy"],
    ["RBC INDICES", "", "", "", ""],
    ["Haemotocrit(PCV)", "", "37.1 Vol%", "36.0-46.0 Vol%", "Impedance"],
    ["Mean Cell Volume (MCV)", "", "79.3 fl", "80 97", "Calculation"],
    ["Mean Cell Haemoglobin (MCH)", "", "25.6 pg", "26.5 33.5", "Calculation"],
    ["Mean Cell Haemoglobin Concentration (MCHC) DIFFERENTIAL COUNT", "", "32.3 gms%",
     "31.5 35.0", "Calculation"],
    ["Neutrophils", "", "63.5 %", "35 70 %", ""],
    ["Lymphocytes", "", "29.6 %", "20-40 %", "VCS/ Light microscopy"],
    ["PERIPHERAL SMEAR", "", "", "", ""],
]


@pytest.fixture
def client():
    """A client with no AWS wiring — only the parsing path is exercised."""
    return TextractClient(client=object())


# ── Column-role detection ───────────────────────────────────────────────────
class TestColumnRoles:
    def test_roles_are_read_from_the_header_row(self):
        roles, header_index = _column_roles(CMR_CBC_TABLE)
        assert header_index == 0
        assert roles["label"] == 0 and roles["result"] == 2 and roles["reference"] == 3

    def test_unit_column_is_detected_when_present(self):
        rows = [["Test Name", "Result", "Unit", "Reference Range"],
                ["Haemoglobin", "12.0", "g/dL", "12.0-15.0"]]
        roles, _ = _column_roles(rows)
        assert roles["unit"] == 2

    def test_headerless_table_reports_no_roles(self):
        roles, header_index = _column_roles([["Haemoglobin", "12.0"], ["MCV", "79.3"]])
        assert roles == {} and header_index == -1


# ── Result-cell parsing ─────────────────────────────────────────────────────
class TestValueAndUnit:
    @pytest.mark.parametrize(
        "cell,value,unit",
        [("12.0 gm%", 12.0, "gm%"),
         ("4.68", 4.68, ""),
         ("79.3 fl", 79.3, "fl"),
         ("37.1 Vol%", 37.1, "Vol%"),
         ("63.5 %", 63.5, "%"),
         ("0,4", 0.4, ""),                              # decimal comma still handled
         ("2,88,000 Cells/cumm", 288000.0, "Cells/cumm"),
         ("< 200", 200.0, "")],
    )
    def test_parses_value_and_unit(self, cell, value, unit):
        assert _split_value_and_unit(cell) == (value, unit)

    def test_reference_bleed_is_trimmed_from_the_unit(self):
        """OCR glues a stray reference digit onto the unit: "1.21 lakhs/cummt.5"."""
        value, unit = _split_value_and_unit("1.21 lakhs/cummt.5")
        assert value == 1.21
        assert unit.startswith("lakhs/cumm")
        assert "1.5" not in unit and "t.5" not in unit

    @pytest.mark.parametrize(
        "cell", ["Normocytic Normochromic", "Clear", "Adequate", "", "Negative"]
    )
    def test_descriptive_cells_yield_no_value(self, cell):
        assert _split_value_and_unit(cell) == (None, "")


# ── Table → biomarker rows ──────────────────────────────────────────────────
class TestRowExtraction:
    def _rows(self, client, table=CMR_CBC_TABLE):
        return client._rows_from_response(_blocks_for(table), page=2)

    def test_extracts_every_numeric_result_row(self, client):
        rows = self._rows(client)
        names = [r["name"] for r in rows]
        assert "Haemoglobin" in names
        # 10 numeric rows: HGB, RBC, WBC, PLT, PCV, MCV, MCH, MCHC, NEUT, LYMPH.
        assert len(rows) == 10         # section headers excluded

    def test_section_headers_are_not_biomarkers(self, client):
        names = [r["name"] for r in self._rows(client)]
        for header in ("COMPLETE BLOOD PICTURE", "RBC INDICES", "PERIPHERAL SMEAR"):
            assert header not in names

    def test_wrapped_mchc_label_is_recovered(self, client):
        """The defect that motivated this provider: the flat-text reader saw only
        "Mean Cell Haemoglobin" on this value's line and lost MCHC entirely."""
        rows = {r["name"]: r for r in self._rows(client)}
        mchc = next(r for name, r in rows.items() if "MCHC" in name)
        assert mchc["value"] == 32.3
        assert mchc["unit"] == "gms%"

    def test_column_drifted_values_are_recovered(self, client):
        """RBC Count had no value on its own line for the flat-text reader."""
        rows = {r["name"]: r for r in self._rows(client)}
        assert rows["RBC Count"]["value"] == 4.68

    def test_units_come_from_their_own_column_or_cell(self, client):
        rows = {r["name"]: r for r in self._rows(client)}
        assert rows["Haemoglobin"]["unit"] == "gm%"
        assert rows["Haemotocrit(PCV)"]["unit"] == "Vol%"
        assert rows["Mean Cell Volume (MCV)"]["unit"] == "fl"

    def test_lab_reference_range_is_captured(self, client):
        rows = {r["name"]: r for r in self._rows(client)}
        assert rows["Haemoglobin"]["reference_range"] == "12.0-15.0 gm%"

    def test_page_number_is_recorded(self, client):
        assert all(r["page"] == 2 for r in self._rows(client))

    def test_confidence_comes_from_the_table_block(self, client):
        assert all(r["confidence"] == 0.985 for r in self._rows(client))

    def test_explicit_unit_column_wins_over_embedded_text(self, client):
        table = [["Test Name", "Result", "Unit", "Reference Range"],
                 ["Haemoglobin", "12.0", "g/dL", "12.0-15.0"]]
        row = client._rows_from_response(_blocks_for(table), page=1)[0]
        assert row["unit"] == "g/dL"

    def test_headerless_table_still_yields_rows(self, client):
        table = [["Haemoglobin", "12.0 gm%"], ["MCV", "79.3 fl"]]
        rows = client._rows_from_response(_blocks_for(table), page=1)
        assert {r["name"] for r in rows} == {"Haemoglobin", "MCV"}


# ── End-to-end through the adapter ──────────────────────────────────────────
class TestAdapterIntegration:
    def test_rows_resolve_to_canonical_codes_with_units(self, client):
        from orchestration.layer1_adapter import Layer1ToLayer2Adapter

        rows = client._rows_from_response(_blocks_for(CMR_CBC_TABLE), page=2)
        resolved, meta = Layer1ToLayer2Adapter.convert_ocr_rows_to_biomarker_dict(rows)

        assert resolved["HGB"] == 12.0
        assert resolved["RBC"] == 4.68
        assert resolved["MCHC"] == 32.3          # was lost entirely by flat text
        assert resolved["MCV"] == 79.3
        assert resolved["PLT"] == 1.21
        assert meta["units"]["HGB"] == "gm%"
        assert meta["units"]["PLT"].startswith("lakhs/cumm")


# ── Failure behaviour ───────────────────────────────────────────────────────
class TestFailures:
    @pytest.mark.asyncio
    async def test_no_tables_raises_a_clear_error(self, client, tmp_path):
        client._rows_from_response = lambda *a, **k: []
        img = tmp_path / "scan.png"
        img.write_bytes(b"\x89PNG" + b"0" * 32)

        async def fake_analyze(path):
            return {"Blocks": []}

        client._analyze = fake_analyze
        with pytest.raises(TextractError, match="no table rows"):
            await client.extract_biomarkers_from_file(str(img))

    @pytest.mark.asyncio
    async def test_oversized_page_is_refused_before_the_api_call(self, tmp_path):
        c = TextractClient(client=object(), max_page_bytes=1000)
        img = tmp_path / "big.png"
        img.write_bytes(b"0" * 5000)
        with pytest.raises(TextractError, match="synchronous limit"):
            await c._analyze(str(img))

    def test_textract_error_is_an_ocr_error(self):
        """The API's 502 mapping keys off OCRSpaceError; both providers share it."""
        from orchestration.ocr_space_client import OCRSpaceError

        assert issubclass(TextractError, OCRSpaceError)
