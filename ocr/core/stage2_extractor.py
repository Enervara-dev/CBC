"""
Stage 2 — Extraction (OCR-first)
================================
Primary engine by input type:

  digital PDF (has a text layer) → pdfplumber   — exact text, no OCR needed
  scanned PDF / image            → PaddleOCR     — render → OCR → row reconstruction

Classical OCR reads these clean, high-contrast lab reports accurately. The
structured rows produced here feed stage3 (parsing) and, later, the deterministic
normalization / canonical-schema stage.
"""

from typing import List, Dict, Any
import numpy as np

OCR_RENDER_DPI = 300   # OCR benefits from sharper text


def extract(pdf_path: str, pdf_type: str) -> Dict[str, Any]:
    """
    Unified extraction entry point.

    Args:
        pdf_path:  Path to the PDF (or image — PyMuPDF opens both).
        pdf_type:  "digital" or "scanned" (from stage1).

    Returns:
        {"tables": [...], "raw_text": str, "method": str, ...}
    """
    if pdf_type == "digital":
        result = _extract_pdfplumber(pdf_path)
        if result["tables"] or result["raw_text"].strip():
            return result
        print("[extract] Digital PDF had no extractable text layer — running OCR.")

    return _extract_paddleocr_from_path(pdf_path)


# ── Digital: pdfplumber ──────────────────────────────────────────────────────

def _extract_pdfplumber(pdf_path: str) -> Dict[str, Any]:
    """Exact text + table extraction for digital PDFs (no OCR)."""
    import pdfplumber
    all_tables, raw_text_parts = [], []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                clean = [[cell or "" for cell in row] for row in table if any(row)]
                if clean:
                    all_tables.append(clean)
            text = page.extract_text()
            if text:
                raw_text_parts.append(text)

    return {
        "tables": all_tables,
        "raw_text": "\n".join(raw_text_parts),
        "method": "pdfplumber",
    }


# ── Scanned / image: PaddleOCR ───────────────────────────────────────────────

def _extract_paddleocr_from_path(pdf_path: str) -> Dict[str, Any]:
    """Render every page to an image, then OCR it."""
    from core.stage1_pdf_detector import extract_page_images
    images = extract_page_images(pdf_path, dpi=OCR_RENDER_DPI)
    return _extract_paddleocr(images)


def _extract_paddleocr(page_images: List[np.ndarray]) -> Dict[str, Any]:
    """OCR a list of page images and reconstruct table rows from box geometry."""
    from preprocessing.ocr_engine import extract_text, reconstruct_table_rows
    all_tables, raw_text_parts, all_ocr_items = [], [], []

    for img in page_images:
        items = extract_text(img)
        all_ocr_items.extend(items)
        if not items:
            continue
        rows = reconstruct_table_rows(items)
        if rows:
            all_tables.append(rows)
        raw_text_parts.append(" ".join(item["text"] for item in items))

    return {
        "tables": all_tables,
        "raw_text": "\n".join(raw_text_parts),
        "ocr_items": all_ocr_items,
        "method": "paddleocr",
    }


# ── Legacy shims so older callers keep working ───────────────────────────────

def extract_digital(pdf_path: str) -> Dict[str, Any]:
    return extract(pdf_path, "digital")


def extract_scanned(page_images: List[np.ndarray]) -> Dict[str, Any]:
    """Legacy shim — images already rendered, OCR them directly."""
    return _extract_paddleocr(page_images)
