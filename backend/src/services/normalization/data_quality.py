"""
Data-quality checks for CBC biomarker values — outlier / impossibility / critical
("panic") value detection. Pure and static; no database dependency.

Two tiers of checks:
  1. ABSOLUTE_LIMITS  — the physiologically possible range. A value outside this
     is almost certainly an extraction or unit error, so the value is marked
     ``valid=False`` and ``critical_flag=True``.
  2. CRITICAL_VALUES  — possible but life-threatening values. These stay
     ``valid=True`` (the number is real) but are marked ``critical_flag=True``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class DataQualityChecker:
    """
    Static outlier / impossibility / critical-value checker for CBC biomarkers.

    Class constants define the thresholds; all methods are static so the checker
    can be used without instantiation and without any I/O.
    """

    # Physiologically possible bounds — outside ⇒ impossible (extraction/unit error)
    ABSOLUTE_LIMITS: Dict[str, Dict[str, float]] = {
        "hemoglobin": {"min": 0.5, "max": 25.0},
        "wbc":        {"min": 0.1, "max": 100.0},
        "platelets":  {"min": 1,   "max": 2000},
        "hematocrit": {"min": 1,   "max": 90},
    }

    # Life-threatening ("panic") thresholds — real but require urgent attention
    CRITICAL_VALUES: Dict[str, Dict[str, float]] = {
        "hemoglobin": {"low": 3.0, "high": 20.0},
        "wbc":        {"low": 0.5, "high": 50.0},
        "platelets":  {"low": 10,  "high": 1500},
    }

    # Pretty names for human-readable messages
    DISPLAY_NAMES: Dict[str, str] = {
        "hemoglobin": "Hemoglobin",
        "wbc": "WBC",
        "platelets": "Platelets",
        "hematocrit": "Hematocrit",
    }

    @staticmethod
    def check_biomarker(biomarker_id: str, value: float, unit: str) -> Dict[str, Any]:
        """
        Check a single biomarker value for impossibility and critical thresholds.

        Parameters
        ----------
        biomarker_id : str
            Biomarker name/key (case-insensitive), e.g. ``"hemoglobin"``.
        value : float
            The measured value (assumed already in the standard unit).
        unit : str
            The unit of ``value`` (accepted for interface completeness / logging;
            thresholds are defined in each biomarker's standard unit).

        Returns
        -------
        Dict[str, Any]
            ``{"valid": bool, "quality_issues": List[str],
               "critical_flag": bool, "reason": Optional[str]}``.

        Notes
        -----
        An *absolute-limit* breach makes the value ``valid=False`` (impossible) and
        supersedes the critical-value check. A *critical-value* breach keeps the
        value ``valid=True`` but sets ``critical_flag=True``.

        Examples
        --------
        >>> DataQualityChecker.check_biomarker("hemoglobin", 13.5, "g/dL")
        {'valid': True, 'quality_issues': [], 'critical_flag': False, 'reason': None}
        >>> r = DataQualityChecker.check_biomarker("hemoglobin", 0.2, "g/dL")
        >>> r["valid"], r["critical_flag"], r["reason"]
        (False, True, 'Hemoglobin 0.2 is below absolute minimum 0.5')
        """
        bid = biomarker_id.strip().lower()
        name = DataQualityChecker.DISPLAY_NAMES.get(bid, biomarker_id)
        issues: list[str] = []
        critical_flag = False
        valid = True

        # ── 1. Absolute limits (physiological possibility) ───────────────────
        impossible = False
        limits = DataQualityChecker.ABSOLUTE_LIMITS.get(bid)
        if limits is not None:
            if value < limits["min"]:
                issues.append(f"{name} {value} is below absolute minimum {limits['min']}")
                valid, critical_flag, impossible = False, True, True
            elif value > limits["max"]:
                issues.append(f"{name} {value} is above absolute maximum {limits['max']}")
                valid, critical_flag, impossible = False, True, True

        # ── 2. Critical ("panic") values — only if the value is possible ─────
        if not impossible:
            crit = DataQualityChecker.CRITICAL_VALUES.get(bid)
            if crit is not None:
                if value < crit["low"]:
                    issues.append(f"{name} {value} is critically low (below {crit['low']})")
                    critical_flag = True
                elif value > crit["high"]:
                    issues.append(f"{name} {value} is critically high (above {crit['high']})")
                    critical_flag = True

        reason = issues[0] if issues else None
        result = {
            "valid": valid,
            "quality_issues": issues,
            "critical_flag": critical_flag,
            "reason": reason,
        }
        logger.debug("DataQualityChecker: %s=%s %s -> %s", bid, value, unit, result)
        return result

    # ── Optional helpers ─────────────────────────────────────────────────────
    @staticmethod
    def _is_physiologically_impossible(biomarker_id: str, value: float) -> bool:
        """True if ``value`` is outside the biomarker's absolute (possible) range."""
        limits = DataQualityChecker.ABSOLUTE_LIMITS.get(biomarker_id.strip().lower())
        if limits is None:
            return False
        return value < limits["min"] or value > limits["max"]

    @staticmethod
    def _is_critical(biomarker_id: str, value: float) -> bool:
        """True if ``value`` breaches the biomarker's critical (panic) thresholds."""
        crit = DataQualityChecker.CRITICAL_VALUES.get(biomarker_id.strip().lower())
        if crit is None:
            return False
        return value < crit["low"] or value > crit["high"]
