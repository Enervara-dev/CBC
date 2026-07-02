"""
Tests for the Layer-1 OCR.space client.

Covers the text→row parser (pure), config/error handling, and the full
``extract_biomarkers_from_file`` path against a mocked OCR.space HTTP transport —
asserting the rows match the schema Layer 2 expects: ``{name, value, unit,
confidence}``. No network and no API key required.
"""

import httpx
import pytest

from orchestration.ocr_space_client import (
    OCRSpaceClient,
    OCRSpaceError,
    DEFAULT_ROW_CONFIDENCE,
)


def _client(**kw) -> OCRSpaceClient:
    return OCRSpaceClient(api_key="test-key", **kw)


class TestConfig:
    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("OCR_SPACE_API_KEY", raising=False)
        with pytest.raises(OCRSpaceError):
            OCRSpaceClient(api_key="")

    def test_endpoint_default(self):
        assert _client().endpoint == "https://api.ocr.space/parse/image"


class TestParseText:
    def test_parses_name_value_unit(self):
        text = "Hemoglobin 10.2 g/dL\nWBC 5.2 10^3/uL\nPlatelets 250 K/uL"
        rows = _client()._parse_text(text)
        by_name = {r["name"]: r for r in rows}
        assert by_name["Hemoglobin"]["value"] == 10.2
        assert by_name["Hemoglobin"]["unit"] == "g/dL"
        assert by_name["Platelets"]["value"] == 250.0
        assert all(r["confidence"] == DEFAULT_ROW_CONFIDENCE for r in rows)

    def test_skips_lines_without_name_or_number(self):
        text = "COMPLETE BLOOD COUNT\n12345\nMCV 88 fL\n   \nName Value Unit"
        rows = _client()._parse_text(text)
        names = {r["name"] for r in rows}
        assert "MCV" in names
        # A header line with no number, and a number-only line, are dropped.
        assert "COMPLETE BLOOD COUNT" not in names

    def test_handles_value_with_leading_marker(self):
        rows = _client()._parse_text("Hemoglobin <5.0 g/dL")
        assert rows and rows[0]["value"] == 5.0

    def test_rows_are_consumable_by_layer1_adapter(self):
        from orchestration.layer1_adapter import Layer1ToLayer2Adapter

        text = "Hemoglobin 10.2 g/dL\nMCV 75 fL\nRDW 16.5 %\nWBC 5.2 10^3/uL\nPlatelets 250 K/uL"
        rows = _client()._parse_text(text)
        codes, meta = Layer1ToLayer2Adapter.convert_ocr_rows_to_biomarker_dict(rows)
        assert {"HGB", "MCV", "RDW", "WBC", "PLT"} <= set(codes)


class TestPayloadValidation:
    def test_errored_payload_raises(self):
        with pytest.raises(OCRSpaceError):
            OCRSpaceClient._text_from_payload(
                {"IsErroredOnProcessing": True, "ErrorMessage": ["bad key"]}
            )

    def test_empty_text_raises(self):
        with pytest.raises(OCRSpaceError):
            OCRSpaceClient._text_from_payload(
                {"IsErroredOnProcessing": False, "OCRExitCode": 1, "ParsedResults": []}
            )

    def test_concatenates_parsed_text(self):
        text = OCRSpaceClient._text_from_payload(
            {
                "IsErroredOnProcessing": False,
                "OCRExitCode": 1,
                "ParsedResults": [{"ParsedText": "HGB 10\n"}, {"ParsedText": "MCV 80\n"}],
            }
        )
        assert "HGB 10" in text and "MCV 80" in text


@pytest.mark.asyncio
class TestExtractEndToEnd:
    async def test_extract_against_mocked_ocrspace(self, tmp_path, monkeypatch):
        report = tmp_path / "report.pdf"
        report.write_bytes(b"%PDF-1.4 fake")

        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(
                200,
                json={
                    "IsErroredOnProcessing": False,
                    "OCRExitCode": 1,
                    "ParsedResults": [{"ParsedText": "Hemoglobin 10.2 g/dL\nMCV 75 fL\n"}],
                },
            )

        transport = httpx.MockTransport(handler)
        real_async_client = httpx.AsyncClient

        def fake_async_client(*args, **kwargs):
            kwargs["transport"] = transport
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)

        rows = await _client().extract_biomarkers_from_file(str(report))
        assert captured["url"] == "https://api.ocr.space/parse/image"
        assert {r["name"] for r in rows} == {"Hemoglobin", "MCV"}

    async def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            await _client().extract_biomarkers_from_file("does_not_exist.pdf")
