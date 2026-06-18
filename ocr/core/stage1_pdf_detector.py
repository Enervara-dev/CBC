"""
Stage 1 — PDF Type Detection
Determines if a PDF has a real text layer (digital) or is image-only (scanned).
"""

from typing import Literal
import fitz  # PyMuPDF


MIN_TEXT_CHARS_PER_PAGE = 50


def detect(pdf_path: str) -> dict:
    """
    Returns:
        {
            "type": "digital" | "scanned",
            "page_count": int,
            "text_pages": [page indices with text],
        }
    """
    doc = fitz.open(pdf_path)
    page_count = len(doc)
    text_pages = []

    for i, page in enumerate(doc):
        text = page.get_text().strip()
        if len(text) >= MIN_TEXT_CHARS_PER_PAGE:
            text_pages.append(i)

    doc.close()

    pdf_type: Literal["digital", "scanned"] = (
        "digital" if len(text_pages) > 0 else "scanned"
    )

    return {
        "type": pdf_type,
        "page_count": page_count,
        "text_pages": text_pages,
    }


def extract_page_images(pdf_path: str, dpi: int = 200):
    """
    Render each PDF page to a full-color (RGB) numpy array.

    RGB so PaddleOCR gets the full-color render (it reads color and grayscales
    internally). The OCR wrapper handles channel conversion + upscaling.

    Returns list of HxWx3 uint8 numpy images.
    """
    import numpy as np

    doc = fitz.open(pdf_path)
    images = []
    mat = fitz.Matrix(dpi / 72, dpi / 72)

    for page in doc:
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
        if pix.n == 4:  # drop alpha channel if present
            img = img[:, :, :3]
        images.append(np.ascontiguousarray(img))

    doc.close()
    return images
