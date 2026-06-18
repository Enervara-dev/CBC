"""
OCR engine wrapper around PaddleOCR (the primary extraction engine).
Returns structured text with bounding boxes and confidence scores, and
reconstructs table rows from bounding-box geometry.

Robust to:
  - input as grayscale / RGB / RGBA numpy arrays (our renderer emits RGB)
  - small images (upscaled so the detector can resolve text)
  - PaddleOCR constructor/return-shape differences across 2.6–2.8
"""

import cv2
import numpy as np
from typing import List, Dict, Any

_paddle_ocr = None

# Upscale so the shorter side reaches this — small renders starve the detector.
_MIN_OCR_SIDE = 1000


def _enable_os_trust_store():
    """
    Use the OS trust store for HTTPS so PaddleOCR's model download works behind a
    corporate proxy / custom root CA (certifi's bundle lacks the intercepting CA;
    the OS store has it). No-op if truststore isn't installed.
    """
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:
        pass


def _get_ocr():
    global _paddle_ocr
    if _paddle_ocr is None:
        _enable_os_trust_store()
        from paddleocr import PaddleOCR
        # show_log / use_angle_cls were renamed/removed in newer releases;
        # fall back gracefully so any installed 2.x works.
        try:
            _paddle_ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
        except TypeError:
            try:
                _paddle_ocr = PaddleOCR(use_angle_cls=True, lang="en")
            except TypeError:
                _paddle_ocr = PaddleOCR(lang="en")
    return _paddle_ocr


def _to_bgr_upscaled(image: np.ndarray) -> np.ndarray:
    """Normalize to 3-channel BGR (what cv2/Paddle expect) and upscale if small."""
    if image.ndim == 2:
        img = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.shape[2] == 4:
        img = cv2.cvtColor(image, cv2.COLOR_RGBA2BGR)
    else:
        # our renderer produces RGB → convert to BGR
        img = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

    h, w = img.shape[:2]
    if min(h, w) < _MIN_OCR_SIDE:
        scale = _MIN_OCR_SIDE / min(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)),
                         interpolation=cv2.INTER_CUBIC)
    return img


def extract_text(image: np.ndarray) -> List[Dict[str, Any]]:
    """
    Run PaddleOCR on the image.
    Returns list of: {text, bbox: [x1,y1,x2,y2], confidence}
    """
    ocr = _get_ocr()
    img = _to_bgr_upscaled(image)

    try:
        result = ocr.ocr(img, cls=True)
    except TypeError:
        result = ocr.ocr(img)

    items: List[Dict[str, Any]] = []
    for line in _iter_lines(result):
        parsed = _parse_line(line)
        if parsed:
            items.append(parsed)
    return items


def _iter_lines(result):
    """Yield per-line entries across PaddleOCR return-shape variants."""
    if not result:
        return []
    page = result[0] if isinstance(result, (list, tuple)) else result
    return page or []


def _parse_line(line):
    """A line is typically [bbox, (text, conf)]; tolerate minor variations."""
    try:
        bbox_raw, text_conf = line[0], line[1]
        text, conf = text_conf[0], text_conf[1]
    except (TypeError, IndexError, ValueError):
        return None
    xs = [p[0] for p in bbox_raw]
    ys = [p[1] for p in bbox_raw]
    return {
        "text": str(text).strip(),
        "bbox": [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))],
        "confidence": float(conf),
    }


def reconstruct_table_rows(items: List[Dict], row_tolerance: int = 12) -> List[List[str]]:
    """
    Group OCR items into rows by clustering on Y-center coordinate.
    Returns list of rows, each row a list of text strings (left→right).
    """
    if not items:
        return []

    def y_center(item):
        return (item["bbox"][1] + item["bbox"][3]) / 2

    sorted_items = sorted(items, key=lambda i: (y_center(i), i["bbox"][0]))

    rows: List[List[Dict]] = []
    current_row: List[Dict] = [sorted_items[0]]
    current_y = y_center(sorted_items[0])

    for item in sorted_items[1:]:
        if abs(y_center(item) - current_y) <= row_tolerance:
            current_row.append(item)
        else:
            rows.append(sorted(current_row, key=lambda i: i["bbox"][0]))
            current_row = [item]
            current_y = y_center(item)

    if current_row:
        rows.append(sorted(current_row, key=lambda i: i["bbox"][0]))

    return [[cell["text"] for cell in row] for row in rows]
