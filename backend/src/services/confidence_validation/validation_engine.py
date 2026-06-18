"""
Confidence validation engine (Layer 5 orchestrator).

Takes a Layer 4 :class:`Layer4Output`, applies clinical validation +
impossibility checks + confidence calibration + severity/urgency mapping, and
produces a clinician-ready :class:`Layer5Output`.

Steps (each isolated; a failure is logged, recorded in ``error_messages``, and
the run continues with status ``"partial"``):
  1. impossible-condition checks   → conflict flags
  2. per-finding rule validation   → pass/fail + notes
  3. confidence calibration        → original → final + factors
  4. severity → urgency mapping     → critical/urgent flags + escalations
  5. final findings + audit trail
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from models.graph_schemas import Layer4Output
from models.validation_schemas import (
    ClinicalFlag,
    FinalFinding,
    Layer5Output,
    ValidationAuditTrail,
)
from services.confidence_validation.confidence_calibrator import (
    ConfidenceCalibrator,
    SEVERITY_ORDER,
)

# graph/Layer-2 biomarker code → severity-threshold key
_CODE_TO_BIOMARKER = {
    "HGB": "hemoglobin", "WBC": "wbc", "PLT": "platelets", "HCT": "hematocrit",
    "RBC": "rbc", "MCV": "mcv", "MCH": "mch", "MCHC": "mchc", "RDW": "rdw",
}


class ConfidenceValidationEngine:
    """Orchestrates Layer-5 clinical validation into a Layer5Output."""

    def __init__(
        self,
        confidence_calibrator: ConfidenceCalibrator,
        validation_rules: Dict[str, Any],
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.calibrator = confidence_calibrator
        self.rules = validation_rules
        self.logger = logger or logging.getLogger(__name__)

    # ── Main orchestration ───────────────────────────────────────────────────
    async def validate(self, layer4_output: Layer4Output) -> Layer5Output:
        """Run the 5-step validation pipeline and return a Layer5Output."""
        start = datetime.now(timezone.utc)
        errors: List[str] = []
        checks_performed: List[str] = []
        flags: Dict[str, List[ClinicalFlag]] = {
            "critical": [], "urgent": [], "routine": [], "CONFLICTS": []
        }

        findings = layer4_output.validated_findings
        present_conditions = self._present_conditions(layer4_output)
        biomarker_values = self._biomarker_values(layer4_output)
        self.logger.info("Validation started: %d findings, %d present conditions",
                         len(findings), len(present_conditions))

        # STEP 1 — impossible conditions
        impossible_flagged: List[str] = []
        try:
            for conflict in self.calibrator.check_impossible_conditions(present_conditions):
                impossible_flagged.append(conflict["rule_id"])
                flags["CONFLICTS"].append(ClinicalFlag(
                    flag_id=f"conflict_{conflict['rule_id']}",
                    flag_type=conflict["severity"],
                    finding_id=" + ".join(conflict["conditions"]),
                    message=f"Impossible/contradictory combination: {' + '.join(conflict['conditions'])}.",
                    action_required=conflict["action"],
                    escalation_level="manual_review",
                ))
            checks_performed.append("impossible_check")
            self.logger.info("STEP 1: %d impossible-condition conflicts", len(impossible_flagged))
        except Exception as exc:
            self.logger.error("STEP 1 (impossible conditions) failed: %s", exc)
            errors.append(f"impossible_conditions: {exc}")

        # STEP 2 — validate each finding
        validations: Dict[str, Dict[str, Any]] = {}
        try:
            for finding in findings:
                validations[finding.finding_id] = self.calibrator.validate_against_rules(
                    finding.finding_id, present_conditions
                )
            checks_performed.append("evidence_check")
            self.logger.info("STEP 2: validated %d findings (%d passed)",
                             len(findings), sum(v["passed"] for v in validations.values()))
        except Exception as exc:
            self.logger.error("STEP 2 (rule validation) failed: %s", exc)
            errors.append(f"rule_validation: {exc}")

        # severity per finding (used by both calibration and urgency mapping)
        severities = {f.finding_id: self._finding_severity(f, biomarker_values) for f in findings}

        # STEP 3 — calibrate confidence
        adjustments: Dict[str, Any] = {}
        try:
            for finding in findings:
                validation = validations.get(finding.finding_id, {})
                consistency = "conflicting" if validation.get("contradictions") else "all_consistent"
                cal = self.calibrator.calibrate_confidence(
                    original_confidence=finding.confidence,
                    evidence_count=len(finding.evidence_chain),
                    consistency=consistency,
                    severity=severities.get(finding.finding_id, "routine"),
                )
                adjustments[finding.finding_id] = await self._build_confidence_adjustment_report(
                    cal["original"], cal["final"], cal["factors"]
                )
                self.logger.debug("Confidence %s: %.3f → %.3f", finding.finding_id,
                                  cal["original"], cal["final"])
            checks_performed.append("consistency_check")
            self.logger.info("STEP 3: calibrated %d confidences", len(adjustments))
        except Exception as exc:
            self.logger.error("STEP 3 (calibration) failed: %s", exc)
            errors.append(f"calibration: {exc}")

        # STEP 4 — severity → urgency mapping + flags
        try:
            for finding in findings:
                severity = severities.get(finding.finding_id, "routine")
                if severity in ("critical", "urgent"):
                    final_conf = adjustments.get(finding.finding_id, {}).get("final", finding.confidence)
                    escalation = await self._get_escalation_level(severity, final_conf)
                    urgency = self.rules["urgency_flags"].get(severity, {}).get("urgency", "ROUTINE")
                    flags[severity].append(ClinicalFlag(
                        flag_id=f"flag_{finding.finding_id}_{severity}",
                        flag_type=severity,
                        finding_id=finding.finding_id,
                        message=f"{finding.finding_name}: {severity.upper()} — {urgency} review required.",
                        action_required=urgency,
                        escalation_level=escalation,
                    ))
                    self.logger.warning("Escalation: %s is %s (%s)", finding.finding_id, severity, escalation)
            checks_performed.append("severity_check")
            self.logger.info("STEP 4: %d critical, %d urgent flags",
                             len(flags["critical"]), len(flags["urgent"]))
        except Exception as exc:
            self.logger.error("STEP 4 (severity mapping) failed: %s", exc)
            errors.append(f"severity_mapping: {exc}")

        # STEP 5 — build final findings + audit trail
        final_findings: List[FinalFinding] = []
        for finding in findings:
            validation = validations.get(finding.finding_id, {"passed": True, "rules_applied": []})
            severity = severities.get(finding.finding_id, "routine")
            final_conf = adjustments.get(finding.finding_id, {}).get("final", finding.confidence)
            if not validation.get("passed", True):
                status = "REQUIRES_MANUAL_REVIEW"
            elif severity in ("critical", "urgent"):
                status = "FLAGGED_FOR_ESCALATION"
            else:
                status = "APPROVED_FOR_REVIEW"

            ff = FinalFinding(
                finding_id=finding.finding_id,
                finding_name=finding.finding_name,
                final_confidence=final_conf,
                severity=severity,
                clinical_notes="",
                validation_passed=validation.get("passed", True),
                validation_rules_applied=validation.get("rules_applied", []),
                source_evidence=[link.narrative for link in finding.evidence_chain],
                status=status,
            )
            ff.clinical_notes = await self._generate_clinical_notes(
                ff, [link.model_dump() for link in finding.evidence_chain]
            )
            final_findings.append(ff)

        review_status = await self._determine_review_status(final_findings)
        review_required = review_status != "APPROVED_FOR_REVIEW" or bool(flags["CONFLICTS"])
        next_steps = await self._generate_next_steps(final_findings)

        end = datetime.now(timezone.utc)
        execution_time_ms = round((end - start).total_seconds() * 1000.0, 3)
        audit_trail = ValidationAuditTrail(
            timestamp=start.isoformat(),
            layer4_input=layer4_output.model_dump(),
            validation_checks_performed=checks_performed,
            rules_applied_count=len(validations) + len(impossible_flagged),
            findings_validated=sum(1 for ff in final_findings if ff.validation_passed),
            impossible_conditions_flagged=impossible_flagged,
            confidence_adjustments=adjustments,
            clinician_approvals_required=sum(
                1 for ff in final_findings if ff.status != "APPROVED_FOR_REVIEW"
            ),
            escalations_triggered=len(flags["critical"]) + len(flags["urgent"]),
            execution_time_ms=execution_time_ms,
        )

        if errors:
            status = "partial"
        elif review_required:
            status = "requires_manual_review"
        else:
            status = "success"

        self.logger.info("Validation finished in %.3f ms (status=%s)", execution_time_ms, status)
        return Layer5Output(
            final_findings=final_findings,
            clinical_flags=flags,
            audit_trail=audit_trail,
            status=status,
            clinician_review_required=review_required,
            next_steps=next_steps,
            error_messages=errors,
        )

    # ── Helpers ──────────────────────────────────────────────────────────────
    async def _determine_review_status(self, findings: List[FinalFinding]) -> str:
        """Aggregate review disposition for the batch."""
        if any(not f.validation_passed for f in findings):
            return "REQUIRES_MANUAL_REVIEW"
        if any(f.status == "FLAGGED_FOR_ESCALATION" for f in findings):
            return "FLAGGED_FOR_ESCALATION"
        return "APPROVED_FOR_REVIEW"

    async def _generate_clinical_notes(
        self, finding: FinalFinding, evidence: List[Dict[str, Any]]
    ) -> str:
        """Generate a human-readable clinical explanation for a finding."""
        support = "; ".join(e.get("narrative", "") for e in evidence if e.get("narrative"))
        head = (f"{finding.finding_name} — {finding.severity} "
                f"(confidence {finding.final_confidence:.0%}).")
        if not finding.validation_passed:
            return f"{head} VALIDATION INCOMPLETE — requires manual review. " + (
                f"Supporting evidence: {support}." if support else "Insufficient evidence."
            )
        body = f" Supported by: {support}." if support else ""
        return f"{head}{body}"

    async def _generate_next_steps(self, findings: List[FinalFinding]) -> List[str]:
        """Return actionable next steps for the clinician."""
        steps: List[str] = []
        for f in findings:
            if f.severity == "critical":
                steps.append(f"URGENT: review {f.finding_name} and arrange hematology consult.")
            elif f.severity == "urgent":
                steps.append(f"Review {f.finding_name} within 24h; specialist review advised.")
            elif not f.validation_passed:
                steps.append(f"Manually review {f.finding_name} (validation incomplete).")
            else:
                steps.append(f"Routine review of {f.finding_name}.")
        if not steps:
            steps.append("No findings to action; routine monitoring.")
        return steps

    async def _get_escalation_level(self, severity: str, confidence: float) -> Optional[str]:
        """Map severity (+confidence) to an escalation path."""
        escalation = self.rules["urgency_flags"].get(severity, {}).get("escalation")
        return escalation.lower() if isinstance(escalation, str) else None

    async def _build_confidence_adjustment_report(
        self, original_confidence: float, final_confidence: float, factors: Dict[str, float]
    ) -> Dict[str, Any]:
        """Return a detailed breakdown of a confidence adjustment."""
        adjustment_percent = (
            round((final_confidence - original_confidence) / original_confidence * 100, 1)
            if original_confidence else 0.0
        )
        return {
            "original": round(original_confidence, 4),
            "final": round(final_confidence, 4),
            "adjustment_percent": adjustment_percent,
            "factors": factors,
        }

    # ── Layer-4 input extraction ─────────────────────────────────────────────
    def _present_conditions(self, layer4_output: Layer4Output) -> List[str]:
        """Finding ids + binary feature ids known to hold (from the embedded L3 input)."""
        present = {f.finding_id for f in layer4_output.validated_findings}
        layer3 = layer4_output.audit_trail.layer3_input or {}
        for feature in layer3.get("generated_features", []) or []:
            if isinstance(feature, dict) and str(feature.get("feature_type", "")).endswith("BINARY"):
                fid = feature.get("feature_id")
                if fid:
                    present.add(fid)
        return [p for p in present if p]

    def _biomarker_values(self, layer4_output: Layer4Output) -> Dict[str, float]:
        """Recover {biomarker_id: value} from the embedded Layer 3 input."""
        layer3 = layer4_output.audit_trail.layer3_input or {}
        values: Dict[str, float] = {}
        for biomarker in layer3.get("normalized_biomarkers", []) or []:
            if isinstance(biomarker, dict) and biomarker.get("biomarker_id") is not None:
                values[biomarker["biomarker_id"]] = biomarker.get("value")
        return values

    def _finding_severity(self, finding: Any, biomarker_values: Dict[str, float]) -> str:
        """Worst severity across the finding's evidence biomarkers."""
        worst = "normal"
        for link in finding.evidence_chain:
            code = (link.biomarker_id or "").upper()
            name = _CODE_TO_BIOMARKER.get(code, code.lower())
            value = biomarker_values.get(link.biomarker_id)
            if value is None:
                continue
            sev = self.calibrator.apply_severity_calibration(name, value)
            if SEVERITY_ORDER.get(sev, 0) > SEVERITY_ORDER.get(worst, 0):
                worst = sev
        return worst
