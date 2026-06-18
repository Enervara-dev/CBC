"""
Data normalization orchestrator (Layer 2 entry point).

``DataNormalizer`` turns the raw biomarkers extracted by Layer 1 into normalized,
clinically-anchored records by composing three services:

    raw value  ──UnitConverter──►  standard unit
                                    │
                                    ├─ReferenceRangeLookup──►  patient-specific range
                                    └─DataQualityChecker────►  impossibility / panic flags
                                    ▼
                              NormalizedBiomarker (status, deviation, confidence, issues)

Per-biomarker failures are isolated: one bad row is logged and collected in
``validation_issues`` without aborting the batch (partial results are returned).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from services.normalization.unit_converter import UnitConverter
from services.normalization.reference_lookup import ReferenceRangeLookup
from services.normalization.data_quality import DataQualityChecker

# Per-panel vocabulary lives in the domain folder (single source of truth).
from domains.cbc.biomarkers import NAME_TO_CODE, CODE_TO_NAME, CODE_TO_LOINC

logger = logging.getLogger(__name__)


@dataclass
class NormalizedBiomarker:
    """
    A single biomarker after normalization.

    Attributes
    ----------
    biomarker_id : str          canonical code, e.g. "HGB"
    value : float               value converted to the standard unit
    unit : str                  the standard unit
    reference_min / reference_max : Optional[float]   applicable reference interval
    gender : Optional[str]      patient gender used for the lookup
    age_group : str             "pediatric" / "adult" / "elderly"
    lab_source : str            the lab whose range was applied ("Any" if generic)
    status : str                "LOW" / "NORMAL" / "HIGH"
    deviation_from_min : Optional[float]   value - reference_min (standard units)
    deviation_percent : Optional[float]    (value - reference_min) / reference_min * 100
    extraction_confidence : Optional[float]   Layer-1 OCR confidence (passthrough)
    quality_issues : List[str]  messages from DataQualityChecker
    critical_flag : bool        True if a panic / impossible value was detected
    """

    biomarker_id: str
    value: float
    unit: str
    reference_min: Optional[float]
    reference_max: Optional[float]
    gender: Optional[str]
    age_group: str
    lab_source: str
    status: str
    deviation_from_min: Optional[float]
    deviation_percent: Optional[float]
    extraction_confidence: Optional[float]
    quality_issues: List[str] = field(default_factory=list)
    critical_flag: bool = False
    loinc_code: Optional[str] = None              # LOINC code (joins to the graph)


class DataNormalizer:
    """
    Orchestrate unit conversion + reference lookup + quality checks for a batch of
    extracted biomarkers.

    Parameters
    ----------
    db_session : AsyncSession
        Async DB session (kept for completeness / future queries).
    unit_converter : UnitConverter
        The unit-conversion service (class or instance; uses static ``convert``).
    reference_lookup : ReferenceRangeLookup
        The demographic-aware reference-range lookup service.
    """

    def __init__(
        self,
        db_session: AsyncSession,
        unit_converter: UnitConverter,
        reference_lookup: ReferenceRangeLookup,
    ) -> None:
        self.db = db_session
        self.unit_converter = unit_converter
        self.reference_lookup = reference_lookup
        # Per-batch collection of biomarkers that could not be normalized.
        self.validation_issues: List[str] = []

    # ── Public API ───────────────────────────────────────────────────────────
    async def normalize(
        self,
        extracted_biomarkers: List[Dict[str, Any]],
        patient_metadata: Dict[str, Any],
    ) -> List[NormalizedBiomarker]:
        """
        Normalize a batch of extracted biomarkers for one patient.

        Parameters
        ----------
        extracted_biomarkers : List[Dict]
            Each: ``{"name", "value", "unit", "confidence"}`` (value may be a str).
        patient_metadata : Dict
            ``{"gender", "age", "lab_source"?, "condition"?}``.

        Returns
        -------
        List[NormalizedBiomarker]
            One entry per successfully-normalized biomarker. Failures are skipped
            and recorded in ``self.validation_issues`` (the batch never aborts).
        """
        self.validation_issues = []
        results: List[NormalizedBiomarker] = []

        for biomarker in extracted_biomarkers:
            name = biomarker.get("name", "<unknown>")
            try:
                normalized = await self._normalize_biomarker(biomarker, patient_metadata)
                results.append(normalized)
                logger.info(
                    "Normalized %s -> %s %s [%s]%s",
                    name, normalized.value, normalized.unit, normalized.status,
                    " CRITICAL" if normalized.critical_flag else "",
                )
            except Exception as exc:  # isolate per-biomarker failures
                message = f"Failed to normalize {name!r}: {exc}"
                logger.warning(message)
                self.validation_issues.append(message)
                continue

        return results

    # ── Per-biomarker pipeline ───────────────────────────────────────────────
    async def _normalize_biomarker(
        self,
        biomarker: Dict[str, Any],
        patient_metadata: Dict[str, Any],
    ) -> NormalizedBiomarker:
        """Run the full normalization pipeline for one biomarker (steps 1–7)."""
        # 1. Extract raw fields
        name = biomarker.get("name", "")
        raw_value = biomarker.get("value")
        raw_unit = biomarker.get("unit")
        confidence = biomarker.get("confidence")

        value = float(raw_value)  # raises ValueError on non-numeric → caught upstream

        code = self._get_biomarker_id(name)
        canonical_name = CODE_TO_NAME.get(code, name.strip().lower())

        # 2. Unit conversion → canonical standard unit. For a biomarker the
        #    converter knows, ALWAYS emit the standard unit so downstream unit
        #    comparisons are reliable (e.g. Layer 4's g/dL↔g/L threshold check —
        #    an empty/variant unit must never leave a value un-normalized):
        #      - raw unit recognized (case-insensitively) → convert to standard;
        #      - raw unit missing or unrecognized → assume already in standard unit.
        std_value, std_unit = value, (raw_unit or "")
        if canonical_name in UnitConverter.CONVERSION_FACTORS:
            factors = UnitConverter.CONVERSION_FACTORS[canonical_name]
            raw = (raw_unit or "").strip()
            match = next((u for u in factors if u.lower() == raw.lower()), None) if raw else None
            if match:
                std_value, std_unit = self.unit_converter.convert(canonical_name, value, match)
            else:
                std_value, std_unit = value, UnitConverter.STANDARD_UNITS[canonical_name]

        # 3. Reference range (patient-aware, with fallback)
        gender = patient_metadata.get("gender")
        age = patient_metadata.get("age")
        lab_source = patient_metadata.get("lab_source", "Any")
        condition = patient_metadata.get("condition")
        ref = await self.reference_lookup.get_reference_range(
            code, gender, age, lab_source, condition
        )
        ref_min = ref.get("ref_min")
        ref_max = ref.get("ref_max")

        # 4. Deviation from the lower bound
        deviation_from_min, deviation_percent = self._deviation(std_value, ref_min)

        # 5. Status
        status = self._determine_status(std_value, ref_min, ref_max)

        # 6. Quality checks
        quality = DataQualityChecker.check_biomarker(canonical_name, std_value, std_unit)

        # 7. Assemble
        return NormalizedBiomarker(
            biomarker_id=code,
            value=std_value,
            unit=std_unit,
            reference_min=ref_min,
            reference_max=ref_max,
            gender=gender,
            age_group=self._get_age_group(age),
            lab_source=ref.get("lab_source", lab_source),
            status=status,
            deviation_from_min=deviation_from_min,
            deviation_percent=deviation_percent,
            extraction_confidence=confidence,
            quality_issues=quality["quality_issues"],
            critical_flag=quality["critical_flag"],
            loinc_code=CODE_TO_LOINC.get(code),
        )

    # ── Helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _get_biomarker_id(name: str) -> str:
        """
        Map an extracted biomarker name (or synonym, or code) to its canonical code.

        Raises
        ------
        ValueError
            If the name cannot be resolved to a known biomarker.
        """
        key = name.strip().lower()
        if key in NAME_TO_CODE:
            return NAME_TO_CODE[key]
        upper = name.strip().upper()
        if upper in CODE_TO_NAME:
            return upper
        raise ValueError(f"Unknown biomarker name {name!r}")

    @staticmethod
    def _get_age_group(age: int) -> str:
        """Map an age to a coarse group: pediatric (<18) / adult (18–65) / elderly (>65)."""
        if age < 18:
            return "pediatric"
        if age <= 65:
            return "adult"
        return "elderly"

    @staticmethod
    def _determine_status(
        value: float,
        ref_min: Optional[float],
        ref_max: Optional[float],
    ) -> str:
        """Classify ``value`` against the reference interval as LOW / NORMAL / HIGH."""
        if ref_min is not None and value < ref_min:
            return "LOW"
        if ref_max is not None and value > ref_max:
            return "HIGH"
        return "NORMAL"

    @staticmethod
    def _deviation(value: float, ref_min: Optional[float]):
        """Return ``(deviation_from_min, deviation_percent)`` relative to ``ref_min``."""
        if ref_min is None:
            return None, None
        deviation_from_min = round(value - ref_min, 4)
        if ref_min == 0:
            return deviation_from_min, None
        deviation_percent = round((value - ref_min) / ref_min * 100, 2)
        return deviation_from_min, deviation_percent
