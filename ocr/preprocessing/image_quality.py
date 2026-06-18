"""
Image quality checks before OCR:
- Blur detection via Laplacian variance
- Brightness check
- Tilt detection + auto-correction via Hough lines
"""

import cv2
import numpy as np


class LowQualityImageError(Exception):
    pass


BLUR_THRESHOLD = 80.0       # Laplacian variance below this → too blurry
BRIGHTNESS_LOW = 50         # Mean pixel value below this → too dark
BRIGHTNESS_HIGH = 220       # Mean pixel value above this → overexposed
MAX_AUTO_DESKEW_ANGLE = 15  # Degrees; beyond this ask user to retake


def check_and_preprocess(image: np.ndarray) -> np.ndarray:
    """
    Run all quality checks and auto-correct where possible.
    Raises LowQualityImageError if the image is unrecoverable.
    Returns the corrected image.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image.copy()

    _check_blur(gray)
    gray = _check_brightness(gray)
    gray = _deskew(gray)

    return gray


def _check_blur(gray: np.ndarray):
    score = cv2.Laplacian(gray, cv2.CV_64F).var()
    if score < BLUR_THRESHOLD:
        raise LowQualityImageError(
            f"Image quality is too low for reliable medical interpretation "
            f"(blur score: {score:.1f}, minimum: {BLUR_THRESHOLD}). "
            "Please upload a clearer image with proper lighting and full report visibility."
        )


def _check_brightness(gray: np.ndarray) -> np.ndarray:
    mean_brightness = gray.mean()
    if mean_brightness < BRIGHTNESS_LOW:
        # Attempt CLAHE enhancement
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
    elif mean_brightness > BRIGHTNESS_HIGH:
        # Mild gamma correction to reduce overexposure
        gamma = 0.7
        table = np.array([(i / 255.0) ** gamma * 255 for i in range(256)], dtype=np.uint8)
        gray = cv2.LUT(gray, table)
    return gray


def _deskew(gray: np.ndarray) -> np.ndarray:
    angle = _detect_skew_angle(gray)
    if abs(angle) < 0.5:
        return gray
    if abs(angle) > MAX_AUTO_DESKEW_ANGLE:
        raise LowQualityImageError(
            f"Document tilt ({angle:.1f}°) exceeds auto-correction limit ({MAX_AUTO_DESKEW_ANGLE}°). "
            "Please retake the photo with the document flat and properly aligned."
        )
    h, w = gray.shape
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    corrected = cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)
    return corrected


def _detect_skew_angle(gray: np.ndarray) -> float:
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=100,
                             minLineLength=100, maxLineGap=10)
    if lines is None:
        return 0.0

    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if x2 - x1 == 0:
            continue
        angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        # Only use near-horizontal lines (table rows)
        if abs(angle) < 30:
            angles.append(angle)

    if not angles:
        return 0.0
    return float(np.median(angles))
