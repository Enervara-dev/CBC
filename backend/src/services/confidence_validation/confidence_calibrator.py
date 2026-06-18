"""
Confidence calibrator (Layer 5).

The logic layer over ``validation_rules.py``: applies clinical validation rules,
impossibility checks, severity classification, and confidence calibration. Pure
and synchronous — the async orchestration lives in ``validation_engine.py``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# severity ordering for "take the worst" comparisons
SEVERITY_ORDER = {"normal": 0, "routine": 1, "urgent": 2, "critical": 3}


class ConfidenceCalibrator:
    """Applies the Layer-5 rule sets to validate and calibrate findings."""

    def __init__(self, validation_rules: Dict[str, Any]) -> None:
        self.severity_thresholds: Dict[str, Any] = validation_rules["severity_thresholds"]
        self.clinical_rules: Dict[str, Any] = validation_rules["clinical_validation_rules"]
        self.impossible: Dict[str, Any] = validation_rules["impossible_conditions"]
        self.calibration: Dict[str, Any] = validation_rules["confidence_calibration"]
        self.urgency: Dict[str, Any] = validation_rules["urgency_flags"]

    # ── Impossibility ────────────────────────────────────────────────────────
    def check_impossible_conditions(self, present_conditions: List[str]) -> List[Dict[str, Any]]:
        """
        Return every impossible-condition rule whose conditions are all present.

        ``present_conditions`` = finding ids + binary feature ids known to hold.
        """
        present = set(present_conditions)
        conflicts: List[Dict[str, Any]] = []
        for rule_id, rule in self.impossible.items():
            if all(cond in present for cond in rule["conditions"]):
                conflicts.append({
                    "rule_id": rule_id,
                    "conditions": rule["conditions"],
                    "action": rule["action"],
                    "severity": rule["severity"],
                })
        return conflicts

    # ── Rule validation ──────────────────────────────────────────────────────
    def validate_against_rules(
        self, finding_id: str, present_conditions: List[str]
    ) -> Dict[str, Any]:
        """
        Validate a finding's evidence against its clinical rule.

        Passes when all ``required_findings`` are present and no
        ``contradictory_findings`` are present. Findings without a rule pass by
        default.
        """
        rule = self.clinical_rules.get(finding_id)
        present = set(present_conditions)
        if rule is None:
            return {
                "passed": True,
                "reason": "No specific validation rule; accepted by default.",
                "rules_applied": [],
                "missing_required": [],
                "contradictions": [],
                "min_confidence": 0.0,
                "severity_factor": 1.0,
            }

        missing = [r for r in rule["required_findings"] if r not in present]
        contradictions = [c for c in rule["contradictory_findings"] if c in present]
        passed = not missing and not contradictions

        if passed:
            reason = (f"All required findings present "
                      f"({', '.join(rule['required_findings'])}).")
        elif missing:
            reason = f"Missing required findings: {', '.join(missing)}."
        else:
            reason = f"Contradictory findings present: {', '.join(contradictions)}."

        return {
            "passed": passed,
            "reason": reason,
            "rules_applied": ["evidence_check"],
            "missing_required": missing,
            "contradictions": contradictions,
            "min_confidence": rule["min_confidence"],
            "severity_factor": rule["severity_factor"],
        }

    # ── Severity ─────────────────────────────────────────────────────────────
    def apply_severity_calibration(self, biomarker: str, value: float) -> str:
        """Classify a biomarker value into 'critical'/'urgent'/'routine'/'normal'."""
        bands = self.severity_thresholds.get(biomarker.strip().lower())
        if not bands:
            return "normal"
        for level in ("critical", "urgent", "routine"):
            band = bands.get(level)
            if not band:
                continue
            low = band.get("low")
            high = band.get("high")
            if low and low[0] <= value < low[1]:
                return level
            if high and high[0] <= value < high[1]:
                return level
        return "normal"

    def map_severity_to_urgency(self, severity: str) -> Dict[str, Optional[str]]:
        """Return the urgency/notification/escalation block for a severity."""
        return self.urgency.get(severity, self.urgency.get("routine", {}))

    # ── Confidence calibration ───────────────────────────────────────────────
    def calibrate_confidence(
        self,
        original_confidence: float,
        evidence_count: int,
        consistency: str = "all_consistent",
        severity: str = "routine",
    ) -> Dict[str, Any]:
        """
        Calibrate a confidence score by evidence count, consistency, and severity.

        Returns ``{"original", "final", "factors": {evidence_count, consistency,
        severity}}``.
        """
        if evidence_count >= 3:
            ec_key = "3_or_more_evidence"
        elif evidence_count == 2:
            ec_key = "2_evidence"
        else:
            ec_key = "1_evidence"

        ec_factor = self.calibration["evidence_count_factor"].get(ec_key, 1.0)
        cons_factor = self.calibration["consistency_factor"].get(consistency, 1.0)
        sev_factor = self.calibration.get("hemoglobin_severity_factor", {}).get(severity, 1.0)

        final = max(0.0, min(1.0, round(original_confidence * ec_factor * cons_factor * sev_factor, 4)))
        return {
            "original": round(original_confidence, 4),
            "final": final,
            "factors": {
                "evidence_count": ec_factor,
                "consistency": cons_factor,
                "severity": sev_factor,
            },
        }
