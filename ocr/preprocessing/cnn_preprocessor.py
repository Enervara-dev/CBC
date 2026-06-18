"""
CNN-style preprocessing for scanned PDFs:
- Denoise, binarize, upscale
- Table region detection using contour analysis
The "CNN" here = spatial layout understanding via OpenCV morphology, not a trained neural net.
For higher accuracy on complex layouts, swap _detect_table_regions() with a LayoutLM call.
"""

import cv2
import numpy as np
from typing import List, Tuple


MIN_RESOLUTION = 768


def preprocess(image: np.ndarray) -> np.ndarray:
    """
    Full preprocessing pipeline for a scanned page image.
    Returns a clean binarized image ready for OCR.
    """
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    gray = _upscale_if_needed(gray)
    gray = _denoise(gray)
    binary = _binarize(gray)
    return binary


def detect_table_regions(image: np.ndarray) -> List[Tuple[int, int, int, int]]:
    """
    Detect table bounding boxes using morphological operations.
    Returns list of (x, y, w, h) rectangles.
    """
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    binary = _binarize(gray)
    inverted = cv2.bitwise_not(binary)

    # Detect horizontal and vertical lines
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 40))

    h_lines = cv2.morphologyEx(inverted, cv2.MORPH_OPEN, horizontal_kernel, iterations=2)
    v_lines = cv2.morphologyEx(inverted, cv2.MORPH_OPEN, vertical_kernel, iterations=2)

    table_mask = cv2.add(h_lines, v_lines)

    # Dilate to merge nearby lines into table blocks
    dilate_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (20, 20))
    table_mask = cv2.dilate(table_mask, dilate_kernel, iterations=3)

    contours, _ = cv2.findContours(table_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    regions = []
    h_img, w_img = gray.shape
    min_area = (h_img * w_img) * 0.01  # ignore tiny blobs

    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if w * h > min_area:
            regions.append((x, y, w, h))

    return sorted(regions, key=lambda r: r[1])  # sort top-to-bottom


def _upscale_if_needed(gray: np.ndarray) -> np.ndarray:
    h, w = gray.shape
    if min(h, w) < MIN_RESOLUTION:
        scale = MIN_RESOLUTION / min(h, w)
        new_w, new_h = int(w * scale), int(h * scale)
        gray = cv2.resize(gray, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
    return gray


def _denoise(gray: np.ndarray) -> np.ndarray:
    return cv2.fastNlMeansDenoising(gray, h=10, templateWindowSize=7, searchWindowSize=21)


def _binarize(gray: np.ndarray) -> np.ndarray:
    # Otsu thresholding gives clean black/white for most lab reports
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary
