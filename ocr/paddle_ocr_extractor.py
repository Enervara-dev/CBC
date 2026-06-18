"""
PaddleOCR extractor — Layer 1 adapter to the OCRRow interface.

Wraps the OCR pipeline so it produces the row shape the orchestration layer wants:
``[{"name", "value", "unit", "confidence"}, ...]`` via
``extract_biomarkers_from_file(path)``.

Reuses the existing, proven OCR plumbing rather than re-initialising PaddleOCR:
  - ``core.stage1_pdf_detector.extract_page_images`` renders PDF/image pages.
  - ``preprocessing.ocr_engine.extract_text`` runs PaddleOCR (lazy singleton,
    truststore-enabled for the corporate proxy, version-robust) and returns
    ``[{"text", "bbox", "confidence"}, ...]``.

Parsing here groups detected text into rows (by y), orders cells (by x), then
pulls name / value / unit / confidence from each row.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# A cell is a "value cell" if it starts with a digit (optionally < / >).
_VALUE_START = re.compile(r"^\s*[<>]?\s*\d")
# First numeric token within a cell (for the actual value).
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


class PaddleOCRExtractor:
    """Turn a PDF/image file into biomarker rows via PaddleOCR."""

    def __init__(
        self,
        confidence_threshold: float = 0.8,
        lang: str = "en",
        dpi: int = 300,
        row_tolerance: int = 12,
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self.lang = lang
        self.dpi = dpi
        self.row_tolerance = row_tolerance

    # ── Public API ───────────────────────────────────────────────────────────
    async def extract_biomarkers_from_file(self, file_path: str) -> List[Dict[str, Any]]:
        """
        Extract biomarker rows from a report file.

        Returns
        -------
        List[Dict[str, Any]]
            ``[{"name", "value", "unit", "confidence"}, ...]``.

        Raises
        ------
        FileNotFoundError
            If the file does not exist.
        ValueError
            If no biomarker rows can be parsed from the OCR output.
        """
        # STEP 1 — validate file
        if not os.path.isfile(file_path):
            logger.error("OCR input file not found: %s", file_path)
            raise FileNotFoundError(f"OCR input file not found: {file_path}")

        # STEP 2 — render + OCR (sync work offloaded to a thread)
        ocr_output = await asyncio.to_thread(self._run_ocr, file_path)
        logger.debug("Raw OCR output (%d items): %s", len(ocr_output), ocr_output)

        # STEP 3 — parse into biomarker rows
        rows = self._extract_table_from_ocr_output(ocr_output)
        biomarkers: List[Dict[str, Any]] = []
        for row in rows:
            parsed = self._parse_biomarker_row(row)
            if parsed is None:
                continue
            if parsed["confidence"] < self.confidence_threshold:
                logger.warning("Low-confidence OCR row (%.2f): %s",
                               parsed["confidence"], parsed["name"])
            biomarkers.append(parsed)

        # STEP 4 — return or raise
        if not biomarkers:
            logger.error("No biomarkers found in OCR output for %s", file_path)
            raise ValueError("No biomarkers found in OCR output")
        logger.info("Extracted %d biomarker rows from OCR", len(biomarkers))
        return biomarkers

    # ── OCR call (reuses ocr_engine + stage1 rendering) ──────────────────────
    def _run_ocr(self, file_path: str) -> List[Dict[str, Any]]:
        """Render each page and OCR it; returns flat detected-text items."""
        from core.stage1_pdf_detector import extract_page_images   # lazy (needs ocr/ on path)
        from preprocessing.ocr_engine import extract_text

        items: List[Dict[str, Any]] = []
        for image in extract_page_images(file_path, dpi=self.dpi):
            items.extend(extract_text(image))
        return items

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _extract_table_from_ocr_output(self, ocr_output: Any) -> List[Dict[str, Any]]:
        """
        Parse PaddleOCR's (possibly nested) output into row dicts.

        Flattens to detected text items, clusters them into rows by y-coordinate,
        orders each row left→right by x, and returns
        ``[{"col1", "col2", ..., "confidence"}, ...]``.

        Row grouping is **skew-tolerant**: the threshold adapts to the median text
        height (phone photos of lab tables are tilted, so a row's left-edge name
        and far-right value land at slightly different y), and the running row
        baseline tracks the group mean so gradual tilt accumulates within one row
        instead of splitting name-only / value-only fragments that then get dropped.
        """
        items = self._flatten(ocr_output)
        if not items:
            return []

        def y_center(item: Dict[str, Any]) -> float:
            return (item["bbox"][1] + item["bbox"][3]) / 2.0

        def height(item: Dict[str, Any]) -> float:
            return abs(item["bbox"][3] - item["bbox"][1])

        # Tolerance: at least the configured floor, but scale to ~0.7× the median
        # text height so it works across resolutions / DPI and tolerates skew.
        heights = sorted(h for h in (height(it) for it in items) if h > 0)
        med_h = heights[len(heights) // 2] if heights else 0.0
        tol = max(float(self.row_tolerance), 0.7 * med_h)

        items.sort(key=lambda it: (y_center(it), it["bbox"][0]))

        rows: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = [items[0]]
        current_y = y_center(items[0])
        for item in items[1:]:
            if abs(y_center(item) - current_y) <= tol:
                current.append(item)
                current_y = sum(y_center(c) for c in current) / len(current)  # running mean
            else:
                rows.append(current)
                current = [item]
                current_y = y_center(item)
        rows.append(current)

        table: List[Dict[str, Any]] = []
        for row in rows:
            row.sort(key=lambda it: it["bbox"][0])
            row_dict: Dict[str, Any] = {f"col{i + 1}": it["text"] for i, it in enumerate(row)}
            confidences = [float(it.get("confidence", 1.0)) for it in row]
            row_dict["confidence"] = round(sum(confidences) / len(confidences), 4) if confidences else 1.0
            table.append(row_dict)
        return table

    @staticmethod
    def _flatten(ocr_output: Any) -> List[Dict[str, Any]]:
        """
        Normalize any PaddleOCR shape into ``[{"text","bbox","confidence"}, ...]``.

        Accepts already-parsed dict items, raw Paddle lines ``[bbox, (text, conf)]``,
        and nested per-page lists.
        """
        items: List[Dict[str, Any]] = []
        for entry in (ocr_output or []):
            if isinstance(entry, dict) and "bbox" in entry:
                items.append(entry)
            elif (isinstance(entry, (list, tuple)) and len(entry) == 2
                  and isinstance(entry[1], (list, tuple)) and len(entry[1]) == 2):
                bbox_raw, (text, conf) = entry
                xs = [p[0] for p in bbox_raw]
                ys = [p[1] for p in bbox_raw]
                items.append({
                    "text": str(text).strip(),
                    "bbox": [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))],
                    "confidence": float(conf),
                })
            elif isinstance(entry, (list, tuple)):
                items.extend(PaddleOCRExtractor._flatten(entry))
        return items

    def _parse_biomarker_row(self, row_dict: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extract ``{name, value, unit, confidence}`` from one OCR row.

        Strategy: the first cell that starts with a number is the value; cells
        before it form the name; the next cell (if alphabetic) is the unit.
        Returns ``None`` if the row has no numeric value or no name.
        """
        col_keys = sorted([k for k in row_dict if k.startswith("col")], key=lambda k: int(k[3:]))
        cells = [str(row_dict[k]).strip() for k in col_keys]
        if not cells:
            return None

        value_idx = next((i for i, cell in enumerate(cells) if _VALUE_START.match(cell)), None)
        if value_idx is None or value_idx == 0:
            return None  # no numeric value, or no name preceding it

        match = _NUMBER.search(cells[value_idx])
        if not match:
            return None
        value = float(match.group().replace(",", ""))

        name = " ".join(cells[:value_idx]).strip()
        if not name:
            return None

        unit = ""
        if value_idx + 1 < len(cells):
            nxt = cells[value_idx + 1]
            if re.search(r"[A-Za-z%/]", nxt) and not _VALUE_START.match(nxt):
                unit = nxt

        return {
            "name": name,
            "value": value,
            "unit": unit,
            "confidence": float(row_dict.get("confidence", 0.9)),
        }
