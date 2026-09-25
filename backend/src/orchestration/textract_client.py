"""
AWS Textract client — Layer 1 extraction with real table structure.

Why this exists alongside ``OCRSpaceClient``
--------------------------------------------
OCR.space returns flat text, so the parser has to guess that a label and a value
belong together because they landed on the same *line*. Real lab reports break
that guess constantly, and three failures were measured on actual reports:

* A wrapped label ("Mean Cell Haemoglobin Concentration (MCHC)" printed across
  two lines) left the value's line reading only "Mean Cell Haemoglobin" —
  indistinguishable from MCH, so MCHC was lost and its value misattributed.
* Values in a column that drifted from their label were dropped entirely
  (albumin, RBC count).
* Stray numbers on a label-ish line became results (the "MC-2657" stamp).

Textract's ``AnalyzeDocument`` with ``FeatureTypes=["TABLES"]`` returns addressed
cells — row, column, and the text within — so label, result, unit and reference
range are read from *their own columns* instead of being guessed. On the CMR
report that beat OCR.space, this recovers MCHC and RBC directly.

Interface
---------
``extract_biomarkers_from_file(path) -> [{name, value, unit, confidence, ...}]``
is identical to ``OCRSpaceClient``'s, so the adapter, orchestrator and Layers 2-6
are unchanged. Rows additionally carry ``reference_range`` (the lab's own stated
interval) and ``page``, which downstream code may use or ignore.

Configuration::

    OCR_PROVIDER=textract        # selects this client (see api.main)
    AWS_REGION=ap-south-1        # Textract is NOT available in every region
    plus standard AWS credentials (env, ~/.aws, or the instance/task role)

Cost and privacy: ``AnalyzeDocument`` with TABLES is billed per page, so a
13-page report is 13 billable pages. Textract is HIPAA-eligible under a BAA,
which flat-text OCR services generally are not — relevant when the documents are
real patient reports.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from orchestration.ocr_space_client import (
    OCRSpaceError,
    _needs_page_split,
    _parse_number,
    _split_pdf_pages,
)

logger = logging.getLogger(__name__)

DEFAULT_REGION = "ap-south-1"
DEFAULT_MAX_PAGE_BYTES = 4_500_000     # Textract sync limit is 5 MB; stay under it

# Column-header vocabulary. Indian and US lab reports both use these words, and
# the header row is what tells us which column holds what.
_LABEL_HEADERS = ("parameter", "test name", "test", "investigation", "analyte", "description")
_RESULT_HEADERS = ("result", "results", "observed value", "observed values", "value", "patient value")
_UNIT_HEADERS = ("unit", "units")
_REFERENCE_HEADERS = ("biological reference", "reference", "reference interval",
                      "reference range", "normal range", "bio. ref. interval", "normal value")
_NOISE_HEADERS = ("method", "specimen", "sample", "remarks", "comment", "flag")

# A value cell often carries its unit: "12.0 gm%", "9.65 X1000cells/cumm", "85.6 fL".
_VALUE_WITH_UNIT = re.compile(
    r"^[<>≤≥]?\s*(\d+(?:[.,]\d+)*)\s*(.*)$"
)
# Text that means "no numeric result" — descriptive rows we should skip.
_NON_NUMERIC = re.compile(r"^[A-Za-z ,.()/-]+$")


class TextractError(OCRSpaceError):
    """Textract was unreachable, refused the document, or returned nothing usable.

    Subclasses :class:`OCRSpaceError` so the API's existing 502 mapping and the
    orchestrator's error handling treat both providers identically.
    """


def _cell_text(block: Dict[str, Any], blocks: Dict[str, Dict[str, Any]]) -> str:
    """Concatenate the WORD/SELECTION children of a CELL block."""
    parts: List[str] = []
    for relationship in block.get("Relationships", []) or []:
        if relationship.get("Type") != "CHILD":
            continue
        for child_id in relationship.get("Ids", []):
            child = blocks.get(child_id)
            if not child:
                continue
            if child.get("BlockType") == "WORD":
                parts.append(child.get("Text", ""))
            elif (child.get("BlockType") == "SELECTION_ELEMENT"
                  and child.get("SelectionStatus") == "SELECTED"):
                parts.append("[X]")
    return " ".join(p for p in parts if p).strip()


def _table_rows(table: Dict[str, Any], blocks: Dict[str, Dict[str, Any]]) -> List[List[str]]:
    """Return the table as a dense list of rows, each a list of cell texts."""
    cells: List[Dict[str, Any]] = []
    for relationship in table.get("Relationships", []) or []:
        if relationship.get("Type") == "CHILD":
            for cell_id in relationship.get("Ids", []):
                cell = blocks.get(cell_id)
                if cell and cell.get("BlockType") == "CELL":
                    cells.append(cell)
    if not cells:
        return []

    grid: Dict[int, Dict[int, str]] = {}
    for cell in cells:
        row = cell.get("RowIndex", 0)
        col = cell.get("ColumnIndex", 0)
        grid.setdefault(row, {})[col] = _cell_text(cell, blocks)

    width = max((max(cols) for cols in grid.values() if cols), default=0)
    return [[grid.get(r, {}).get(c, "") for c in range(1, width + 1)] for r in sorted(grid)]


def _column_roles(rows: List[List[str]]) -> Tuple[Dict[str, int], int]:
    """
    Map column roles from the table's header row.

    Returns ``({role: column index}, header row index)``. Roles are ``label``,
    ``result``, ``unit`` and ``reference``; any may be absent. When no header is
    recognised the mapping is empty and the caller falls back to positional
    heuristics — which is why the fallback must stay sane.
    """
    for row_index, row in enumerate(rows[:4]):        # headers are near the top
        lowered = [c.strip().lower() for c in row]
        roles: Dict[str, int] = {}
        for col_index, text in enumerate(lowered):
            if not text:
                continue
            if "label" not in roles and any(h in text for h in _LABEL_HEADERS):
                roles["label"] = col_index
            elif "result" not in roles and any(h in text for h in _RESULT_HEADERS):
                roles["result"] = col_index
            elif "unit" not in roles and any(h == text or h in text for h in _UNIT_HEADERS):
                roles["unit"] = col_index
            elif "reference" not in roles and any(h in text for h in _REFERENCE_HEADERS):
                roles["reference"] = col_index
        # A header row must at least name the label and the result columns.
        if "label" in roles and "result" in roles:
            return roles, row_index
    return {}, -1


def _split_value_and_unit(cell: str) -> Tuple[Optional[float], str]:
    """
    Pull ``(value, unit)`` out of a result cell such as "12.0 gm%" or "9.65".

    Returns ``(None, "")`` when the cell holds no numeric result — descriptive
    rows ("Normocytic Normochromic", "Clear") are common and must not become
    biomarkers.
    """
    text = (cell or "").strip()
    if not text or _NON_NUMERIC.match(text):
        return None, ""
    match = _VALUE_WITH_UNIT.match(text)
    if not match:
        return None, ""
    try:
        value = _parse_number(match.group(1))
    except ValueError:
        return None, ""
    unit = match.group(2).strip()
    # Trim a reference interval that bled into the result cell — Textract keeps
    # columns apart, but OCR still glues "1.21 lakhs/cumm" to a stray "1.5".
    unit = re.split(r"[\d(]", unit, maxsplit=1)[0].strip(" -:\t")
    return value, unit


class TextractClient:
    """
    Layer-1 extractor backed by AWS Textract table analysis.

    Parameters
    ----------
    region_name :
        AWS region. Textract is not offered everywhere — ``eu-north-1`` has no
        endpoint, for instance — so this defaults to ``ap-south-1`` and can be
        overridden by ``AWS_REGION``.
    """

    def __init__(
        self,
        region_name: Optional[str] = None,
        client: Any = None,
        max_page_bytes: int = DEFAULT_MAX_PAGE_BYTES,
    ) -> None:
        self.region_name = (region_name or os.getenv("AWS_REGION") or DEFAULT_REGION).strip()
        self.max_page_bytes = max_page_bytes
        self._client = client       # injectable for tests; built lazily otherwise

    def _textract(self) -> Any:
        """Build (once) and return the boto3 Textract client."""
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - declared dependency
                raise TextractError(
                    "boto3 is required for the Textract provider. Run: pip install boto3"
                ) from exc
            self._client = boto3.client("textract", region_name=self.region_name)
        return self._client

    # ── Public API (identical to OCRSpaceClient) ─────────────────────────────
    async def extract_biomarkers_from_file(self, file_path: str) -> List[Dict[str, Any]]:
        """
        Extract biomarker rows from a report.

        Returns
        -------
        List[Dict[str, Any]]
            ``{"name", "value", "unit", "confidence", "reference_range", "page"}``
            per detected result row, in document order.
        """
        pages = self._page_files(file_path)
        rows: List[Dict[str, Any]] = []
        try:
            for page_number, page_path in enumerate(pages, start=1):
                response = await self._analyze(page_path)
                rows.extend(self._rows_from_response(response, page_number))
        finally:
            if pages != [file_path]:
                for page_path in pages:
                    try:
                        os.unlink(page_path)
                    except OSError:
                        pass

        logger.info("Textract: %d result row(s) from %d page(s) of %s",
                    len(rows), len(pages), os.path.basename(file_path))
        if not rows:
            raise TextractError(
                f"Textract found no table rows with numeric results in "
                f"{os.path.basename(file_path)}."
            )
        return rows

    def _page_files(self, file_path: str) -> List[str]:
        """
        Single-page files to send.

        Textract's synchronous ``AnalyzeDocument`` accepts only single-page PDFs,
        so multi-page reports are split — the same splitter the OCR.space client
        uses for its size cap.
        """
        if os.path.splitext(file_path)[1].lower() != ".pdf":
            return [file_path]
        pages = _split_pdf_pages(file_path)
        if len(pages) == 1:
            # One page: send the original and let the temp copy go.
            try:
                os.unlink(pages[0])
            except OSError:
                pass
            return [file_path]
        logger.info("Textract: %s split into %d single-page documents",
                    os.path.basename(file_path), len(pages))
        return pages

    async def _analyze(self, page_path: str) -> Dict[str, Any]:
        """Call Textract for one page (off the event loop — boto3 is blocking)."""
        size = os.path.getsize(page_path)
        if size > self.max_page_bytes:
            raise TextractError(
                f"Page {os.path.basename(page_path)} is {size} bytes; Textract's "
                f"synchronous limit is {self.max_page_bytes}. Downscale the scan."
            )
        with open(page_path, "rb") as handle:
            payload = handle.read()

        def _call() -> Dict[str, Any]:
            return self._textract().analyze_document(
                Document={"Bytes": payload}, FeatureTypes=["TABLES"]
            )

        try:
            return await asyncio.to_thread(_call)
        except TextractError:
            raise
        except Exception as exc:      # boto3 raises ClientError/BotoCoreError
            raise TextractError(f"Textract call failed for {os.path.basename(page_path)}: {exc}") from exc

    # ── Response → biomarker rows ────────────────────────────────────────────
    def _rows_from_response(self, response: Dict[str, Any], page: int) -> List[Dict[str, Any]]:
        """Turn one page's Textract blocks into biomarker rows."""
        blocks = {b["Id"]: b for b in response.get("Blocks", []) if "Id" in b}
        tables = [b for b in response.get("Blocks", []) if b.get("BlockType") == "TABLE"]
        rows: List[Dict[str, Any]] = []
        for table in tables:
            rows.extend(self._rows_from_table(table, blocks, page))
        return rows

    def _rows_from_table(
        self, table: Dict[str, Any], blocks: Dict[str, Dict[str, Any]], page: int
    ) -> List[Dict[str, Any]]:
        """Extract result rows from one table using its header row for column roles."""
        grid = _table_rows(table, blocks)
        if not grid:
            return []
        roles, header_index = _column_roles(grid)
        confidence = round(float(table.get("Confidence", 90.0)) / 100.0, 4)

        out: List[Dict[str, Any]] = []
        for row_index, row in enumerate(grid):
            if row_index <= header_index:
                continue

            if roles:
                label = row[roles["label"]].strip() if roles["label"] < len(row) else ""
                result_cell = row[roles["result"]].strip() if roles["result"] < len(row) else ""
                unit_cell = (row[roles["unit"]].strip()
                             if "unit" in roles and roles["unit"] < len(row) else "")
                reference = (row[roles["reference"]].strip()
                             if "reference" in roles and roles["reference"] < len(row) else "")
            else:
                # No recognisable header: the first cell with text is the label and
                # the first cell that starts with a number is the result.
                label = next((c.strip() for c in row if c.strip()), "")
                result_cell = next(
                    (c.strip() for c in row[1:] if c.strip() and c.strip()[0].isdigit()), ""
                )
                unit_cell = ""
                reference = ""

            if not label or not result_cell:
                continue
            value, embedded_unit = _split_value_and_unit(result_cell)
            if value is None:
                continue                      # section header or descriptive row

            unit = unit_cell or embedded_unit
            if unit:
                unit = re.split(r"[\d(]", unit, maxsplit=1)[0].strip(" -:\t")

            out.append({
                "name": label,
                "value": value,
                "unit": unit,
                "confidence": confidence,
                "reference_range": reference,
                "page": page,
            })
        return out


__all__ = ["TextractClient", "TextractError", "DEFAULT_REGION"]
