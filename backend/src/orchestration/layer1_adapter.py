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

# Canonical biomarker name → code (keys lowercase). Lives in the domain folders so
# adding a specialty is a one-file edit; the registry merges every registered
# panel because an OCR'd report does not announce which panel it is (and may mix
# them). Fuzzy/de-spaced matching is layered on top in ``_resolve_biomarker_code``.
from domains.registry import merged_biomarker_lookup

BIOMARKER_LOOKUP = merged_biomarker_lookup()

logger = logging.getLogger(__name__)

# Below this length a candidate must match an alias exactly (see the fuzzy stage
# of ``_resolve_biomarker_code``).
_MIN_FUZZY_LENGTH = 4

# A parenthesised abbreviation inside a test label, e.g. "... (MCHC)".
_PARENTHESISED = re.compile(r"\(([^)]{2,12})\)")


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

    # (a'') parenthesised abbreviation — the lab's own name for the row.
    # "Mean Cell Haemoglobin Concentration (MCHC)" must resolve to MCHC, not to
    # the generic token "haemoglobin". Stage (c) below scans keys longest-first,
    # so without this the 11-character "haemoglobin" beat the 4-character "mchc"
    # and MCHC was silently recorded as a second haemoglobin.
    for abbreviation in _PARENTHESISED.findall(name or ""):
        candidate = _normalize_name(abbreviation).replace(" ", "")
        if candidate in BIOMARKER_LOOKUP:
            return BIOMARKER_LOOKUP[candidate]

    # (b) fuzzy (try both the spaced and de-spaced forms).
    #
    # Short tokens are excluded: at 2-3 characters the 0.8 cutoff is met by a
    # single-character difference, so unrelated abbreviations collide with real
    # aliases. Two such misreads were found on real reports — "PT" (prothrombin
    # time) matched "plt" at exactly 0.80 and became a platelet count, and the
    # lab's "MC-2657" registration stamp matched "mcv" and became an MCV of 2657,
    # displacing the genuine MCV further down the report. Every short alias the
    # tables carry (hb, hct, plt, mcv, alt, hdl, …) is an exact key, so requiring
    # an exact or de-spaced match for them costs nothing.
    for candidate in (norm, despaced):
        if len(candidate) < _MIN_FUZZY_LENGTH:
            continue
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
            The biomarker dict and resolution metadata. The metadata carries
            ``units`` (code → the unit printed on the report), which Layer 2 needs
            to convert the value — without it every value is assumed to already be
            in the pipeline's standard unit, and a platelet count printed as
            "2.91 lakhs/cumm" is read as 2.91 K/uL. It also carries
            ``repeated_biomarkers``: see the note on duplicates below.

        Raises
        ------
        ValueError
            If no biomarkers from any registered panel can be resolved.

        Duplicates
        ----------
        A code seen more than once keeps its FIRST value, because rows arrive in
        page order and the first panel in a document is the one being reported.
        That is a real assumption, not a detail: a PDF containing two draws (a
        follow-up appended to an earlier visit) otherwise yields a silent blend of
        both. Every repeat is therefore recorded in ``repeated_biomarkers``.

        A repeat means "this code was resolved from more than one line", which has
        two quite different causes and the metadata cannot tell them apart:
        genuinely repeated panels (two haemoglobins from two draws), or a line that
        resolved to the same code by accident — an "LDL/HDL Ratio" row matching
        LDL, an "Absolute Neutrophil Count" row matching the neutrophil percentage,
        or a method/reference-range line carrying a stray number. Treat the list as
        "worth a human look", not as proof of a second draw.
        """
        biomarker_dict: Dict[str, float] = {}
        confidence_scores: Dict[str, float] = {}
        units: Dict[str, str] = {}
        resolution_notes: List[str] = []
        repeated: Dict[str, List[float]] = {}

        for row in ocr_rows:
            name = row.get("name") if isinstance(row, dict) else getattr(row, "name", None)
            value = row.get("value") if isinstance(row, dict) else getattr(row, "value", None)
            unit = row.get("unit") if isinstance(row, dict) else getattr(row, "unit", None)
            confidence = (
                row.get("confidence") if isinstance(row, dict)
                else getattr(row, "confidence", None)
            )

            code = _resolve_biomarker_code(name or "")
            if code is None:
                logger.debug("Unresolved OCR biomarker name: %r", name)
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                logger.error("Non-numeric value for %s (%r): %r — skipping", code, name, value)
                continue

            if code in biomarker_dict:
                repeated.setdefault(code, [biomarker_dict[code]]).append(numeric)
                logger.info(
                    "Repeated biomarker %s (%r): keeping first value %s, also saw %s",
                    code, name, biomarker_dict[code], numeric,
                )
                continue

            biomarker_dict[code] = numeric
            if unit:
                units[code] = str(unit).strip()
            if confidence is not None:
                confidence_scores[code] = confidence
            resolution_notes.append(f"{name} → {code}")

        if not biomarker_dict:
            raise ValueError("No known biomarkers extracted from OCR")
        if len(biomarker_dict) < 3:
            logger.warning("Only %d biomarker(s) resolved from OCR (partial report)",
                           len(biomarker_dict))
        if repeated:
            logger.warning(
                "Document contains repeated biomarkers %s — it may hold more than one "
                "panel/draw; only the first occurrence of each was analysed.",
                sorted(repeated),
            )

        metadata = {
            "biomarker_codes_found": list(biomarker_dict.keys()),
            "ocr_confidence_scores": confidence_scores,
            "name_resolution_notes": resolution_notes,
            "units": units,
            "repeated_biomarkers": {c: vals for c, vals in sorted(repeated.items())},
        }
        return biomarker_dict, metadata
