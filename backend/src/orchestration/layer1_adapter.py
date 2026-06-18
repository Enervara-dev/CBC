"""
Layer 1 → Layer 2 adapter.

Converts raw OCR rows (Layer 1 output) into the ``{biomarker_code: value}`` dict
the orchestrator expects, resolving messy OCR biomarker names to canonical codes
(HGB, MCV, …) by exact / fuzzy / substring matching.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# Canonical biomarker name → code (keys lowercase). Lives in the domain folder so
# adding a specialty is a one-file edit; fuzzy/de-spaced matching is layered on
# top of this table in ``_resolve_biomarker_code``.
from domains.cbc.biomarkers import BIOMARKER_LOOKUP

logger = logging.getLogger(__name__)


@dataclass
class OCRRow:
    """One biomarker row as produced by Layer 1 OCR."""

    name: str
    value: float
    unit: str
    confidence: float
    loinc_code: Optional[str] = None


def _normalize_name(name: str) -> str:
    """Lowercase, strip punctuation to spaces, and collapse whitespace."""
    s = (name or "").lower().strip()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _resolve_biomarker_code(name: str) -> Optional[str]:
    """
    Map a raw OCR biomarker name to its canonical code.

    Strategy: (a) exact lookup, (b) fuzzy match (cutoff 0.8), (c) substring /
    whole-token match, else ``None``.
    """
    norm = _normalize_name(name)
    if not norm:
        return None

    # (a) exact
    if norm in BIOMARKER_LOOKUP:
        return BIOMARKER_LOOKUP[norm]

    # (a') de-spaced exact — handles OCR-split abbreviations: "M.C.V" → "m c v" →
    # "mcv", "H b" → "hb", "R.D.W" → "rdw".
    despaced = norm.replace(" ", "")
    if despaced in BIOMARKER_LOOKUP:
        return BIOMARKER_LOOKUP[despaced]

    # (b) fuzzy (try both the spaced and de-spaced forms)
    for candidate in (norm, despaced):
        matches = difflib.get_close_matches(candidate, list(BIOMARKER_LOOKUP), n=1, cutoff=0.8)
        if matches:
            return BIOMARKER_LOOKUP[matches[0]]

    # (c) substring (multi-word keys) / whole-token (single-word keys), longest first.
    # Single-word keys match whole tokens only (never substrings) to avoid
    # collisions like "mch" inside "mchc".
    tokens = set(norm.split()) | {despaced}
    for key in sorted(BIOMARKER_LOOKUP, key=len, reverse=True):
        if " " in key:
            if key in norm:
                return BIOMARKER_LOOKUP[key]
        elif key in tokens:
            return BIOMARKER_LOOKUP[key]
    return None


class Layer1ToLayer2Adapter:
    """Static adapter from Layer 1 OCR output to the orchestrator's input dict."""

    @staticmethod
    def convert_ocr_rows_to_biomarker_dict(
        ocr_rows: List[Dict[str, Any]],
    ) -> Tuple[Dict[str, float], Dict[str, Any]]:
        """
        Transform OCR rows into ``({code: value}, metadata)``.

        Parameters
        ----------
        ocr_rows : List[Dict]
            ``[{"name", "value", "unit", "confidence"}, ...]`` (OCRRow also accepted).

        Returns
        -------
        Tuple[Dict[str, float], Dict[str, Any]]
            The biomarker dict and resolution metadata.

        Raises
        ------
        ValueError
            If no CBC biomarkers can be resolved.
        """
        biomarker_dict: Dict[str, float] = {}
        confidence_scores: Dict[str, float] = {}
        resolution_notes: List[str] = []

        for row in ocr_rows:
            name = row.get("name") if isinstance(row, dict) else getattr(row, "name", None)
            value = row.get("value") if isinstance(row, dict) else getattr(row, "value", None)
            confidence = (
                row.get("confidence") if isinstance(row, dict)
                else getattr(row, "confidence", None)
            )

            code = _resolve_biomarker_code(name or "")
            if code is None:
                logger.debug("Unresolved OCR biomarker name: %r", name)
                continue
            if code in biomarker_dict:
                logger.debug("Duplicate biomarker %s (%r) — keeping first occurrence", code, name)
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                logger.error("Non-numeric value for %s (%r): %r — skipping", code, name, value)
                continue

            biomarker_dict[code] = numeric
            if confidence is not None:
                confidence_scores[code] = confidence
            resolution_notes.append(f"{name} → {code}")

        if not biomarker_dict:
            raise ValueError("No CBC biomarkers extracted from OCR")
        if len(biomarker_dict) < 3:
            logger.warning("Only %d biomarker(s) resolved from OCR (partial report)",
                           len(biomarker_dict))

        metadata = {
            "biomarker_codes_found": list(biomarker_dict.keys()),
            "ocr_confidence_scores": confidence_scores,
            "name_resolution_notes": resolution_notes,
        }
        return biomarker_dict, metadata
