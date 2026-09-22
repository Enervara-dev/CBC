"""
Unit conversion for lab biomarker values (every registered panel).

A small, self-contained, deterministic converter — no database required. Each
biomarker has a set of accepted units, each expressed as a multiplicative factor
*relative to that biomarker's standard unit*. Conversion between any two accepted
units is therefore:

    value_in_standard = value * FACTOR[from_unit]
    value_in_target   = value_in_standard / FACTOR[to_unit]
    ⇒ value_in_target = value * FACTOR[from_unit] / FACTOR[to_unit]

The standard unit of each biomarker has factor ``1.0`` and is recorded in
``STANDARD_UNITS`` (used as the default target when ``to_unit`` is omitted).

The factor tables are **panel knowledge**, so they live in each domain's
``units.py`` (``domains/<panel>/units.py``) and are merged here across every
registered domain — CBC, LFT, Lipid, and whatever is registered next. Adding a
biomarker's units is therefore a domain edit, not a service edit.

Examples
--------
>>> UnitConverter.convert("hemoglobin", 100.0, "g/L", "g/dL")
(10.0, 'g/dL')
>>> UnitConverter.convert("wbc", 7.5, "K/uL")
(7.5, 'K/uL')
"""

from __future__ import annotations

import re
import logging
from typing import Dict, Optional, Tuple

from domains.registry import merged_unit_rules

logger = logging.getLogger(__name__)

_UNIT_RULES = merged_unit_rules()


# OCR routinely mangles the "per cubic millimetre" denominator Indian laboratories
# print: a real CMR report came through as "lakhs/cumnt.5" and "X1000cells/curtr".
# Treated as unknown, the platelet count of 1.21 lakhs/cumm (121 K/uL, mildly low)
# was read as 1.21 K/uL and escalated as critical thrombocytopenia. So a
# denominator that starts like "/cu" or "/cm" reads as "/cumm", and spelling
# variants of the numerator fold together.
_CUMM_DENOMINATOR = re.compile(r"/c[um][a-z.\d]*$")
_NUMERATOR_VARIANTS = (("lacs", "lakhs"), ("lakh/", "lakhs/"), ("lac/", "lakhs/"))


def _unit_key(unit: str) -> str:
    """A spelling of ``unit`` that survives common OCR damage (for matching only)."""
    key = re.sub(r"\s+", "", unit.lower())
    key = _CUMM_DENOMINATOR.sub("/cumm", key)
    for variant, canonical in _NUMERATOR_VARIANTS:
        key = key.replace(variant, canonical)
    return key


class UnitConverter:
    """
    Static converter for lab biomarker units, across every registered panel.

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
    Factors are applied verbatim from the merged domain tables — those are the
    single source of truth for the supported analytes (see
    ``domains/<panel>/units.py``).
    """

    # factor = multiplier to convert a value FROM this unit TO the biomarker's
    # standard unit; merged across every registered panel.
    CONVERSION_FACTORS: Dict[str, Dict[str, float]] = _UNIT_RULES["conversion_factors"]

    # standard (canonical) unit per biomarker — the one with factor 1.0
    STANDARD_UNITS: Dict[str, str] = _UNIT_RULES["standard_units"]

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
        src_key = UnitConverter._resolve_unit(biomarker, src)
        dst_key = UnitConverter._resolve_unit(biomarker, dst)
        converted = round(value * factors[src_key] / factors[dst_key], 6)

        logger.info(
            "UnitConverter.convert: %s | %s %s -> %s %s",
            biomarker, value, src, converted, dst_key,
        )
        return converted, dst_key

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
    def _resolve_unit(biomarker_id: str, unit: str) -> Optional[str]:
        """
        Return the table's spelling of ``unit`` for ``biomarker_id``, or ``None``.

        Matching is case-insensitive: reports print "Cells/cumm", "CELLS/CUMM" and
        "cells/cumm" for the same unit, and the table cannot carry every casing.

        Failing that, it is OCR-tolerant (see :func:`_unit_key`) — but only when
        every table unit sharing the tolerant spelling converts identically, so it
        never guesses between two scales.
        """
        factors = UnitConverter.CONVERSION_FACTORS.get(biomarker_id.strip().lower())
        if not factors:
            return None
        wanted = unit.strip().lower()
        exact = next((u for u in factors if u.lower() == wanted), None)
        if exact or not wanted:
            return exact
        key = _unit_key(unit)
        candidates = sorted(u for u in factors if _unit_key(u) == key)
        if candidates and len({factors[u] for u in candidates}) == 1:
            return candidates[0]
        return None

    @staticmethod
    def _validate_unit(biomarker_id: str, unit: str) -> bool:
        """Return ``True`` if ``unit`` is an accepted unit for ``biomarker_id``."""
        return UnitConverter._resolve_unit(biomarker_id, unit) is not None
