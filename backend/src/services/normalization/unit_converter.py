"""
Unit conversion for CBC biomarker values.

A small, self-contained, deterministic converter — no database required. Each
biomarker has a set of accepted units, each expressed as a multiplicative factor
*relative to that biomarker's standard unit*. Conversion between any two accepted
units is therefore:

    value_in_standard = value * FACTOR[from_unit]
    value_in_target   = value_in_standard / FACTOR[to_unit]
    ⇒ value_in_target = value * FACTOR[from_unit] / FACTOR[to_unit]

The standard unit of each biomarker has factor ``1.0`` and is recorded in
``STANDARD_UNITS`` (used as the default target when ``to_unit`` is omitted).

Examples
--------
>>> UnitConverter.convert("hemoglobin", 100.0, "g/L", "g/dL")
(10.0, 'g/dL')
>>> UnitConverter.convert("wbc", 7.5, "K/uL")
(7.5, 'K/uL')
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class UnitConverter:
    """
    Static converter for CBC biomarker units.

    Attributes
    ----------
    CONVERSION_FACTORS : Dict[str, Dict[str, float]]
        ``biomarker -> {unit: factor}`` where ``factor`` multiplies a value in
        that unit to express it in the biomarker's standard unit.
    STANDARD_UNITS : Dict[str, str]
        ``biomarker -> standard unit`` (the ``factor == 1.0`` unit). Used as the
        default conversion target.

    Notes
    -----
    Factors are applied verbatim from the table below — it is the single source
    of truth for the supported CBC analytes.
    """

    # factor = multiplier to convert a value FROM this unit TO the biomarker's standard unit
    CONVERSION_FACTORS: Dict[str, Dict[str, float]] = {
        "hemoglobin": {"g/dL": 1.0, "g/L": 0.1, "mmol/L": 0.621},
        "wbc":        {"K/uL": 1.0, "10^3/uL": 1.0, "10^9/L": 1.0},
        "mcv":        {"fL": 1.0, "um^3": 1.0},
        "mch":        {"pg": 1.0, "fmol": 0.0621},
        "rbc":        {"M/uL": 1.0, "10^6/uL": 1.0, "10^12/L": 1.0},
        "platelets":  {"K/uL": 1.0, "10^3/uL": 1.0, "10^9/L": 1.0},
        "rdw":        {"%": 1.0},
    }

    # standard (canonical) unit per biomarker — the one annotated "(standard)"
    STANDARD_UNITS: Dict[str, str] = {
        "hemoglobin": "g/dL",
        "wbc": "K/uL",
        "mcv": "fL",
        "mch": "pg",
        "rbc": "M/uL",
        "platelets": "K/uL",
        "rdw": "%",
    }

    @staticmethod
    def convert(
        biomarker_id: str,
        value: float,
        from_unit: str,
        to_unit: Optional[str] = None,
    ) -> Tuple[float, str]:
        """
        Convert ``value`` of ``biomarker_id`` from ``from_unit`` to ``to_unit``.

        Parameters
        ----------
        biomarker_id : str
            Biomarker key (case-insensitive), e.g. ``"hemoglobin"``, ``"wbc"``.
        value : float
            The numeric value in ``from_unit``.
        from_unit : str
            Source unit; must be an accepted unit for the biomarker.
        to_unit : Optional[str]
            Target unit. Defaults to the biomarker's standard unit when ``None``.

        Returns
        -------
        Tuple[float, str]
            ``(converted_value, result_unit)``.

        Raises
        ------
        ValueError
            If the biomarker is unknown, or ``from_unit`` / ``to_unit`` is not an
            accepted unit for that biomarker.

        Examples
        --------
        >>> UnitConverter.convert("hemoglobin", 100.0, "g/L", "g/dL")
        (10.0, 'g/dL')
        >>> UnitConverter.convert("wbc", 7.5, "K/uL")
        (7.5, 'K/uL')
        """
        biomarker = biomarker_id.strip().lower()
        if not UnitConverter._validate_biomarker(biomarker):
            raise ValueError(
                f"Unknown biomarker {biomarker_id!r}. "
                f"Known biomarkers: {sorted(UnitConverter.CONVERSION_FACTORS)}"
            )

        src = from_unit.strip()
        dst = to_unit.strip() if to_unit is not None else UnitConverter._get_standard_unit(biomarker)

        if not UnitConverter._validate_unit(biomarker, src):
            raise ValueError(
                f"Unknown unit {from_unit!r} for biomarker {biomarker!r}. "
                f"Accepted units: {sorted(UnitConverter.CONVERSION_FACTORS[biomarker])}"
            )
        if not UnitConverter._validate_unit(biomarker, dst):
            raise ValueError(
                f"Unknown target unit {to_unit!r} for biomarker {biomarker!r}. "
                f"Accepted units: {sorted(UnitConverter.CONVERSION_FACTORS[biomarker])}"
            )

        factors = UnitConverter.CONVERSION_FACTORS[biomarker]
        converted = round(value * factors[src] / factors[dst], 6)

        logger.info(
            "UnitConverter.convert: %s | %s %s -> %s %s",
            biomarker, value, src, converted, dst,
        )
        return converted, dst

    @staticmethod
    def _get_standard_unit(biomarker_id: str) -> str:
        """
        Return the standard (canonical) unit for ``biomarker_id``.

        Raises
        ------
        ValueError
            If the biomarker is unknown.
        """
        biomarker = biomarker_id.strip().lower()
        if not UnitConverter._validate_biomarker(biomarker):
            raise ValueError(f"Unknown biomarker {biomarker_id!r}.")
        return UnitConverter.STANDARD_UNITS[biomarker]

    @staticmethod
    def _validate_biomarker(biomarker_id: str) -> bool:
        """Return ``True`` if ``biomarker_id`` (case-insensitive) is supported."""
        return biomarker_id.strip().lower() in UnitConverter.CONVERSION_FACTORS

    @staticmethod
    def _validate_unit(biomarker_id: str, unit: str) -> bool:
        """Return ``True`` if ``unit`` is an accepted unit for ``biomarker_id``."""
        biomarker = biomarker_id.strip().lower()
        if biomarker not in UnitConverter.CONVERSION_FACTORS:
            return False
        return unit.strip() in UnitConverter.CONVERSION_FACTORS[biomarker]
