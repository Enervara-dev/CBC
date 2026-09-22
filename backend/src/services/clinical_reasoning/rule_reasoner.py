"""
Rule-based clinical reasoner — Layer 4 without the knowledge graph.

Why
---
Layer 4's job is to turn *facts* ("HGB 6.2, LOW, severe") into *findings*
("severe anaemia; reduced oxygen-carrying capacity; check iron/B12/folate; this
level risks cardiac decompensation"). The graph implementation does that by
traversing Neo4j — which is empty for LFT and Lipid and unreachable in most
deployments, so every abnormal result arrived at the clinician as a bare number.

This engine produces the same :class:`Layer4Output` from the domain data that
already exists, so Layers 5 and 6 work unchanged and no result goes unexplained.
Where the graph *is* available, ``CompositeReasoner`` runs both and merges.

Two sources of findings, deliberately
-------------------------------------
1. **Per-biomarker.** Every out-of-range parameter produces a finding, graded by
   how far out it is. This is what guarantees complete coverage: a lone abnormal
   value does not have to form a recognised pattern to be reported and actioned.
2. **Multi-marker conditions.** Combinations that mean more together than apart
   (microcytic anaemia, cholestasis, atherogenic dyslipidaemia) are matched from
   each domain's ``clinical_validation_rules`` and explained from its
   ``clinical_conditions``.

Both carry their evidence chain, so a reader can always see which numbers
produced a statement and disagree with it. Nothing here diagnoses; it prepares
findings for clinician review, which is what Layer 5 then validates and flags.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from domains.clinical_types import SEVERITY_ORDER, worst_severity
from domains.registry import (
    all_domains,
    domain_for_code,
    merged_biomarker_interpretations,
    merged_clinical_conditions,
    merged_code_to_name,
    merged_validation_rules,
)
from models.feature_schemas import Layer3Output
from models.graph_schemas import (
    AuditTrail,
    ConflictAlert,
    EvidenceLink,
    Layer4Output,
    Recommendation,
    ValidatedFinding,
)

logger = logging.getLogger(__name__)

# A measured out-of-range value is a fact, not an inference, so per-biomarker
# findings start high. Multi-marker conditions are inferences and start from the
# rule's own ``min_confidence``.
_MEASURED_CONFIDENCE = 0.95

def _status_text(status: Any) -> str:
    """Render a status as LOW/HIGH/NORMAL whether it is a str or a Pydantic enum."""
    return str(getattr(status, "value", status) or "").upper()


def _severity_for(
    name: str, value: float, thresholds: Dict[str, Any], status: Any = None
) -> str:
    """
    Grade one value against its panel's severity bands.

    Returns ``critical`` / ``urgent`` / ``routine`` / ``normal`` — the vocabulary
    Layer 5's calibrator already uses.

    ``status`` is the verdict against the patient's *own* reference range (sex,
    age, pregnancy), which the generic bands cannot know:

    * LOW / HIGH grades only that side, and is never ``normal``. A man's
      haemoglobin of 12.5 is below his 13.5 limit but inside the generic 12–16
      band; reporting "Anaemia — normal" contradicts itself, so it is ``routine``.
    * NORMAL is ``normal``, whatever band the number would fall in for someone
      else (a child's haemoglobin of 11.5).
    * No status grades both sides, as before.
    """
    direction = _status_text(status).lower()
    if direction == "normal":
        return "normal"
    sides = (direction,) if direction in ("low", "high") else ("low", "high")
    bands = thresholds.get(name) or {}
    for band in ("critical", "urgent", "routine"):
        entry = bands.get(band)
        if not isinstance(entry, dict):
            continue
        for side in sides:
            interval = entry.get(side)
            if interval and interval[0] <= value < interval[1]:
                return band
    return "routine" if direction in ("low", "high") else "normal"


class RuleBasedClinicalReasoner:
    """
    Deterministic Layer-4 engine driven by the domain interpretation tables.

    Parameters
    ----------
    logger :
        Optional logger; one is derived from the module otherwise.
    """

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger
        self._interpretations = merged_biomarker_interpretations()
        self._conditions = merged_clinical_conditions()
        self._rules = merged_validation_rules()
        self._code_to_name = merged_code_to_name()
        self._thresholds = self._rules.get("severity_thresholds", {})
        # Percentage code → absolute-count code (NEUT → ANC), declared per panel.
        self._relative_counts: Dict[str, str] = {
            relative: absolute
            for domain in all_domains()
            for relative, absolute in (domain.metadata.get("relative_counts") or {}).items()
        }

    # ── Public API (matches GraphReasoningEngine) ────────────────────────────
    async def reason(self, layer3_output: Layer3Output) -> Layer4Output:
        """Turn Layer-3 facts into explained, evidenced findings."""
        started = datetime.now(timezone.utc)
        errors: List[str] = []

        biomarkers = list(layer3_output.normalized_biomarkers)
        present_features = {
            f.feature_id for f in layer3_output.generated_features
            if str(f.feature_type).upper().endswith("BINARY")
        }

        findings: List[ValidatedFinding] = []
        recommendations: List[Recommendation] = []

        try:
            marker_findings, marker_recs = self._biomarker_findings(biomarkers)
            findings.extend(marker_findings)
            recommendations.extend(marker_recs)
        except Exception as exc:                     # never lose the whole run
            self.logger.exception("Per-biomarker interpretation failed")
            errors.append(f"biomarker_interpretation: {exc}")

        try:
            condition_findings, condition_recs = self._condition_findings(
                present_features, biomarkers
            )
            findings.extend(condition_findings)
            recommendations.extend(condition_recs)
        except Exception as exc:
            self.logger.exception("Condition matching failed")
            errors.append(f"condition_matching: {exc}")

        findings = self._deduplicate(findings)
        conflicts = self._conflicts({f.finding_id for f in findings})
        recommendations = self._dedupe(recommendations)
        # Worst first, then most confident. An unrecognised band sorts last
        # rather than raising — a reasoner must not fail over a label.
        def _rank(finding: ValidatedFinding) -> Tuple[int, float]:
            band = finding.severity or "normal"
            position = (SEVERITY_ORDER.index(band) if band in SEVERITY_ORDER
                        else len(SEVERITY_ORDER))
            return position, -finding.confidence

        findings.sort(key=_rank)

        elapsed = (datetime.now(timezone.utc) - started).total_seconds() * 1000.0
        self.logger.info(
            "Clinical reasoning: %d finding(s), %d recommendation(s), %d conflict(s) "
            "from %d biomarker(s)",
            len(findings), len(recommendations), len(conflicts), len(biomarkers),
        )

        return Layer4Output(
            validated_findings=findings,
            recommendations=recommendations,
            conflicts=conflicts,
            audit_trail=AuditTrail(
                timestamp=started.isoformat(),
                layer3_input=layer3_output.model_dump(),
                neo4j_queries_executed=[],          # this engine consults no graph
                nodes_queried=0,
                patterns_validated=len(self._conditions),
                findings_extracted=len(findings),
                conflicts_detected=len(conflicts),
                recommendations_generated=len(recommendations),
                execution_time_ms=round(elapsed, 3),
            ),
            status="partial" if errors else "success",
            error_messages=errors,
            diagnostics=self._diagnostics(biomarkers, findings),
        )

    # ── Source 1: every abnormal parameter ───────────────────────────────────
    def _biomarker_findings(
        self, biomarkers: Sequence[Any]
    ) -> Tuple[List[ValidatedFinding], List[Recommendation]]:
        """One finding per out-of-range biomarker, explained and graded."""
        findings: List[ValidatedFinding] = []
        recommendations: List[Recommendation] = []

        present = {b.biomarker_id for b in biomarkers}
        for biomarker in biomarkers:
            status = _status_text(biomarker.status)
            if status not in ("LOW", "HIGH"):
                continue
            code = biomarker.biomarker_id
            absolute = self._relative_counts.get(code)
            if absolute and absolute in present:
                # "Neutrophils 38%" is not neutropenia when the absolute count is
                # 4.6 — the percentage only moved because another lineage rose.
                # The absolute count speaks for this lineage instead.
                continue
            direction = status.lower()
            interpretation = (self._interpretations.get(code) or {}).get(direction)
            if interpretation is None:
                # No authored narrative: still report the abnormality rather than
                # dropping it — silence about an out-of-range value is worse than
                # a bare statement.
                self.logger.debug("No interpretation for %s %s", code, direction)
                interpretation = None

            name = self._code_to_name.get(code, code.lower())
            severity = _severity_for(name, biomarker.value, self._thresholds, biomarker.status)
            finding_id = f"{name}_{direction}_finding"
            display = (interpretation.finding_name if interpretation
                       else f"{name.replace('_', ' ').title()} {direction}")

            narrative = (
                f"{code} = {biomarker.value:g} {biomarker.unit or ''}".rstrip()
                + f" ({status}"
                + (f", reference {biomarker.reference_min:g}–{biomarker.reference_max:g}"
                   if biomarker.reference_min and biomarker.reference_max else "")
                + f") — {display}"
            )
            findings.append(ValidatedFinding(
                finding_id=finding_id,
                finding_name=display,
                confidence=_MEASURED_CONFIDENCE,
                severity=severity,
                evidence_chain=[EvidenceLink(
                    biomarker_id=code,
                    biomarker_name=name.replace("_", " ").title(),
                    evidence_strength=_MEASURED_CONFIDENCE,
                    narrative=narrative,
                )],
                source_pattern=f"biomarker:{code}:{direction}",
                interpretation=interpretation.meaning if interpretation else "",
                consider=list(interpretation.consider) if interpretation else [],
                critical_note=interpretation.critical_note if interpretation else "",
            ))

            if interpretation:
                for rec in interpretation.recommendations:
                    recommendations.append(self._to_recommendation(rec, finding_id, severity))
        return findings, recommendations

    # ── Source 2: multi-marker conditions ────────────────────────────────────
    def _condition_findings(
        self, present_features: set, biomarkers: Sequence[Any]
    ) -> Tuple[List[ValidatedFinding], List[Recommendation]]:
        """Match the domain rule sets and explain what each match means."""
        rules: Dict[str, Any] = self._rules.get("clinical_validation_rules", {})
        by_code = {b.biomarker_id: b for b in biomarkers}
        findings: List[ValidatedFinding] = []
        recommendations: List[Recommendation] = []

        for condition_id, rule in rules.items():
            required = list(rule.get("required_findings") or [])
            if not required or not all(f in present_features for f in required):
                continue
            contradictory = [f for f in (rule.get("contradictory_findings") or [])
                             if f in present_features]
            if contradictory:
                self.logger.debug("Condition %s blocked by %s", condition_id, contradictory)
                continue

            optional = [f for f in (rule.get("optional_findings") or [])
                        if f in present_features]
            condition = self._conditions.get(condition_id)

            # Confidence: the rule's floor, raised by corroborating optional findings.
            base = float(rule.get("min_confidence", 0.7))
            confidence = min(0.99, base + 0.05 * len(optional))
            if condition and condition.threshold_defined:
                # A definition over measured values ("anaemia with MCV < 80") is as
                # certain as the measurements; only inferred causes start lower.
                confidence = _MEASURED_CONFIDENCE

            evidence = self._evidence_for(required + optional, by_code)
            severity_codes = (condition.severity_from if condition else
                              [link.biomarker_id for link in evidence])
            severity = worst_severity([
                _severity_for(self._code_to_name.get(c, c.lower()),
                              by_code[c].value, self._thresholds, by_code[c].status)
                for c in severity_codes if c in by_code
            ])
            if severity == "normal":
                # A condition rests on out-of-range findings, so it is never
                # "normal" even when its grading markers happen to be in range.
                severity = "routine"

            # Threshold-defined conditions must clear their severity gate.
            if condition and condition.min_severity != "normal":
                gate = SEVERITY_ORDER.index(condition.min_severity)
                if SEVERITY_ORDER.index(severity) > gate:
                    self.logger.debug(
                        "Condition %s not fired: severity %s below the %s gate",
                        condition_id, severity, condition.min_severity,
                    )
                    continue

            if condition and condition.severity_floor != "normal":
                severity = worst_severity([severity, condition.severity_floor])

            findings.append(ValidatedFinding(
                finding_id=condition_id,
                finding_name=condition.name if condition else condition_id.replace("_", " ").title(),
                confidence=round(confidence, 4),
                severity=severity,
                evidence_chain=evidence,
                source_pattern=f"rule:{condition_id}"
                               + (":threshold" if condition and condition.threshold_defined else ""),
                interpretation=condition.meaning if condition else "",
                consider=list(condition.consider) if condition else [],
            ))
            if condition:
                for rec in condition.recommendations:
                    recommendations.append(self._to_recommendation(rec, condition_id, severity))

        # A specific reading replaces the generic one it refines (iron deficiency
        # anaemia over microcytic anaemia), together with its follow-up.
        fired = {f.finding_id for f in findings}
        superseded = {
            other
            for condition_id in fired
            for other in getattr(self._conditions.get(condition_id), "supersedes", ())
        } & fired
        if superseded:
            self.logger.debug("Superseded conditions dropped: %s", sorted(superseded))
            findings = [f for f in findings if f.finding_id not in superseded]
            recommendations = [r for r in recommendations if r.from_finding not in superseded]
        return findings, recommendations

    @staticmethod
    def _deduplicate(findings: List[ValidatedFinding]) -> List[ValidatedFinding]:
        """
        Drop a per-biomarker finding when a named condition already says it.

        Both sources legitimately fire for the same abnormality — "Thrombocytopenia"
        arrives once from the platelet count and once from the condition rule — and
        a clinician should see it once, from the richer source.
        """
        by_name: Dict[str, ValidatedFinding] = {}
        for finding in findings:
            key = finding.finding_name.strip().lower()
            existing = by_name.get(key)
            if existing is None:
                by_name[key] = finding
                continue
            existing_is_rule = existing.source_pattern.startswith("rule:")
            candidate_is_rule = finding.source_pattern.startswith("rule:")
            if existing_is_rule != candidate_is_rule:
                rule, measured = (finding, existing) if candidate_is_rule else (existing, finding)
                by_name[key] = RuleBasedClinicalReasoner._merge_measured(rule, measured)
            elif finding.confidence > existing.confidence:
                by_name[key] = finding
        return list(by_name.values())

    @staticmethod
    def _merge_measured(rule: ValidatedFinding, measured: ValidatedFinding) -> ValidatedFinding:
        """
        Combine a named condition with the measured abnormality it restates.

        The two share a name only when the condition *is* that one abnormality —
        "Low HDL cholesterol" from the rule and from HDL = 39 itself. The rule's
        narrative and follow-up win, but the finding stays an **observation**: it
        keeps the measured confidence and a ``biomarker:`` source, which Layer 5
        reads to skip the single-evidence discount. Taking the rule finding alone
        reported a directly measured HDL of 39 at 47% confidence.
        """
        rank = {band: i for i, band in enumerate(SEVERITY_ORDER)}
        worst = min((rule.severity or "normal", measured.severity or "normal"),
                    key=lambda band: rank.get(band, len(SEVERITY_ORDER)))
        return rule.model_copy(update={
            "confidence": max(rule.confidence, measured.confidence),
            "severity": worst,
            "source_pattern": f"{measured.source_pattern}+{rule.source_pattern}",
            "critical_note": rule.critical_note or measured.critical_note,
            "consider": rule.consider or measured.consider,
            "evidence_chain": measured.evidence_chain or rule.evidence_chain,
        })

    def _evidence_for(
        self, feature_ids: Sequence[str], by_code: Dict[str, Any]
    ) -> List[EvidenceLink]:
        """Build evidence links from the biomarkers behind a set of feature ids."""
        from domains.registry import merged_fact_to_biomarker

        fact_map = merged_fact_to_biomarker()
        links: List[EvidenceLink] = []
        seen: set = set()
        for feature_id in feature_ids:
            target = fact_map.get(feature_id)
            codes = target if isinstance(target, list) else ([target] if target else [])
            for code in codes:
                if code in seen or code not in by_code:
                    continue
                seen.add(code)
                biomarker = by_code[code]
                name = self._code_to_name.get(code, code.lower())
                links.append(EvidenceLink(
                    biomarker_id=code,
                    biomarker_name=name.replace("_", " ").title(),
                    evidence_strength=0.9,
                    narrative=(f"{code} = {biomarker.value:g} {biomarker.unit or ''}".rstrip()
                               + f" ({_status_text(biomarker.status)})"),
                ))
        return links

    # ── Conflicts, recommendations, diagnostics ──────────────────────────────
    def _conflicts(self, finding_ids: set) -> List[ConflictAlert]:
        """Flag mutually exclusive findings declared by the domains."""
        alerts: List[ConflictAlert] = []
        for rule_id, rule in (self._rules.get("impossible_conditions") or {}).items():
            conditions = list(rule.get("conditions") or [])
            present = [c for c in conditions if c in finding_ids]
            if len(present) < 2:
                continue
            alerts.append(ConflictAlert(
                finding1_id=present[0],
                finding1_name=present[0].replace("_", " ").title(),
                finding2_id=present[1],
                finding2_name=present[1].replace("_", " ").title(),
                conflict_severity=str(rule.get("severity", "medium")),
                recommendation="MANUAL_REVIEW",
                note=f"{rule_id}: these findings cannot coexist — check for a "
                     "transcription or unit error before acting.",
            ))
        return alerts

    @staticmethod
    def _to_recommendation(rec: Any, finding_id: str, severity: str) -> Recommendation:
        """Convert a domain recommendation into the Layer-4 schema."""
        urgency = rec.urgency
        if severity == "critical" and urgency == "routine" and rec.priority <= 2:
            # A critical value lifts the key follow-up (priority 1-2), not every
            # suggestion: coeliac serology does not become urgent because the
            # haemoglobin is 5.8.
            urgency = "urgent"
        return Recommendation(
            recommendation_id=rec.recommendation_id,
            recommendation_name=rec.name,
            recommendation_type=rec.recommendation_type,
            priority=rec.priority,
            urgency=urgency,
            from_finding=finding_id,
        )

    @staticmethod
    def _dedupe(recommendations: List[Recommendation]) -> List[Recommendation]:
        """
        Keep one instance of each recommendation, most urgent first.

        Two recommendations are the same action when their ids match *or* their
        headline does — the text before any " — " explanation or "(…)" detail.
        The per-biomarker and condition tables each carry their own wording, so
        a real lipid report listed "Regular aerobic exercise" twice and an
        anaemia panel listed three variants of "Iron studies". Of duplicates, the
        most urgent wins, then the higher priority, then the fuller wording.
        """
        rank = {"stat": 0, "urgent": 1, "routine": 2}

        def headline(rec: Recommendation) -> str:
            text = rec.recommendation_name.split(" — ")[0].split(" (")[0]
            return " ".join(text.lower().split())

        def better(a: Recommendation, b: Recommendation) -> bool:
            return ((rank.get(a.urgency, 3), a.priority, -len(a.recommendation_name))
                    < (rank.get(b.urgency, 3), b.priority, -len(b.recommendation_name)))

        best: Dict[str, Recommendation] = {}
        alias: Dict[str, str] = {}              # id or headline → key in ``best``
        for rec in recommendations:
            key = alias.get(rec.recommendation_id) or alias.get(headline(rec))
            if key is None:
                key = rec.recommendation_id
                best[key] = rec
            elif better(rec, best[key]):
                best[key] = rec
            alias[rec.recommendation_id] = key
            alias[headline(rec)] = key
        return sorted(best.values(), key=lambda r: (rank.get(r.urgency, 3), r.priority))

    def _diagnostics(
        self, biomarkers: Sequence[Any], findings: Sequence[ValidatedFinding]
    ) -> List[str]:
        """Explain a zero-finding run rather than returning a silent empty list."""
        if findings:
            return []
        if not biomarkers:
            return ["No normalized biomarkers reached Layer 4."]
        panels = {domain_for_code(b.biomarker_id) for b in biomarkers}
        return [
            f"All {len(biomarkers)} biomarker(s) were within their reference ranges "
            f"(panels: {', '.join(sorted(p for p in panels if p))}); no abnormality to report."
        ]


__all__ = ["RuleBasedClinicalReasoner"]
