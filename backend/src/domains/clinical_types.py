"""
Clinical interpretation contracts — what an abnormal result *means*.

The pipeline could already say "HGB 6.2 g/dL is LOW, severity severe". It could
not say *so what*. That gap is what these types close, and they are deliberately
data, not code: the reasoning engine is panel-agnostic and reads everything here
through the domain registry, exactly like the biomarker and unit tables.

Two levels of interpretation, because clinicians read a report both ways:

``BiomarkerInterpretation``
    Per marker, per direction. Guarantees **every** abnormal parameter is
    explained — not just the ones that happen to form a recognised pattern. A
    lone low haemoglobin still gets "anaemia; reduced oxygen-carrying capacity;
    check iron/B12/folate", graded by how low it is.

``ClinicalCondition``
    A named condition inferred from several markers together (iron-deficiency
    anaemia, cholestasis, atherogenic dyslipidaemia). Matching rules come from
    the domain's existing ``clinical_validation_rules``; this adds the meaning
    and the follow-up.

Everything here is decision support for a clinician, never a diagnosis: each
condition carries the evidence that produced it so the reader can disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Severity bands, worst first. This is Layer 5's vocabulary
# (``confidence_calibrator.SEVERITY_ORDER``) and the one the severity-threshold
# tables are keyed by — critical / urgent / routine / normal. Keeping a second,
# differently-named scale here would silently mis-sort findings.
SEVERITY_ORDER: Tuple[str, ...] = ("critical", "urgent", "routine", "normal")

# Recommendation kinds understood by ``models.graph_schemas.Recommendation``.
RECOMMENDATION_TYPES: Tuple[str, ...] = ("TEST", "REFERRAL", "MONITOR", "ACTION")

# Urgency vocabulary understood by Layer 5.
URGENCY_LEVELS: Tuple[str, ...] = ("routine", "urgent", "stat")


@dataclass(frozen=True)
class Recommendation:
    """One follow-up action attached to a finding."""

    recommendation_id: str
    name: str
    recommendation_type: str = "TEST"      # TEST / REFERRAL / MONITOR / ACTION
    priority: int = 5                      # 1-10, lower is more urgent
    urgency: str = "routine"               # routine / urgent / stat


@dataclass(frozen=True)
class BiomarkerInterpretation:
    """
    What an out-of-range value of one biomarker means, per direction.

    Attributes
    ----------
    finding_name :
        Clinical label for the abnormality, e.g. "Anaemia" — not the biomarker
        name. This is what a reader sees as the finding.
    meaning :
        One or two sentences: what the abnormality indicates physiologically and
        what commonly causes it.
    consider :
        Differential diagnoses / causes worth considering, most likely first.
    recommendations :
        Follow-up appropriate to this abnormality alone.
    critical_note :
        Extra sentence appended when the value reaches a critical band — the
        "this needs attention now" statement.
    """

    finding_name: str
    meaning: str
    consider: List[str] = field(default_factory=list)
    recommendations: List[Recommendation] = field(default_factory=list)
    critical_note: str = ""


@dataclass(frozen=True)
class ClinicalCondition:
    """
    A named condition inferred from a combination of findings.

    ``condition_id`` matches a key in the domain's ``clinical_validation_rules``,
    which supplies required / optional / contradictory findings and the minimum
    confidence. This record supplies the clinical meaning and the follow-up.

    Attributes
    ----------
    severity_from :
        Biomarker codes whose severity grades this condition. The worst band
        across them wins. Empty means "grade from whatever evidence matched".
    min_severity :
        Lowest severity band at which this condition may fire. Binary features
        only say "out of range", which is too coarse for a condition defined by a
        threshold: "LDL high" is true from 100 mg/dL, but familial
        hypercholesterolaemia is suspected from 190, and recommending cascade
        screening of a patient's relatives off a mildly raised LDL would be
        wrong. Gating on the severity band supplies the missing precision.
        ``"normal"`` (the default) means no gate.
    supersedes :
        Conditions this one makes redundant when both fire. A blood count that
        says "iron deficiency anaemia" should not also say "microcytic anaemia":
        the specific reading replaces the generic one, and the generic one still
        reports on its own when the indices cannot separate the causes.
    threshold_defined :
        True when the condition is a *definition* over measured values rather
        than an inference about a cause: "platelets < 50", "anaemia with MCV
        < 80", "triglycerides >= 500", "ALT >= 10 x ULN". It is then as certain
        as the measurements — reported at measured confidence, and not
        discounted by Layer 5 for resting on few biomarkers (a triglyceride of
        1450 is not "48% likely"). Named causes ("iron deficiency (probable)")
        stay inferences.
    severity_floor :
        Lowest grade the condition is reported at, whatever its markers say.
        Pancytopenia with each line only mildly reduced still needs prompt
        haematology review; grading it "routine" from the separate counts would
        hide exactly the point. ``"normal"`` (the default) means no floor.
    """

    condition_id: str
    name: str
    meaning: str
    consider: List[str] = field(default_factory=list)
    recommendations: List[Recommendation] = field(default_factory=list)
    severity_from: List[str] = field(default_factory=list)
    urgency: str = "routine"
    min_severity: str = "normal"
    supersedes: List[str] = field(default_factory=list)
    threshold_defined: bool = False
    severity_floor: str = "normal"


def worst_severity(bands: List[str]) -> str:
    """Return the most severe band present (``"normal"`` when none are)."""
    for band in SEVERITY_ORDER:
        if band in bands:
            return band
    return "normal"


def validate_interpretations(
    interpretations: Dict[str, Dict[str, BiomarkerInterpretation]],
    known_codes: Dict[str, str],
) -> List[str]:
    """Return problems with an interpretation table (empty == consistent)."""
    problems: List[str] = []
    for code, directions in interpretations.items():
        if code not in known_codes:
            problems.append(f"interpretation for unknown biomarker code {code!r}")
        for direction in directions:
            if direction not in ("low", "high"):
                problems.append(f"{code}: unknown direction {direction!r} (expected low/high)")
    return problems


def validate_conditions(
    conditions: Dict[str, ClinicalCondition],
    rules: Dict[str, object],
) -> List[str]:
    """
    Return problems with a condition table (empty == consistent).

    A condition with no matching rule can never fire; a rule with no condition
    fires but has nothing to say. Both are drift worth failing on.
    """
    problems: List[str] = []
    for condition_id, condition in conditions.items():
        if condition_id != condition.condition_id:
            problems.append(
                f"condition keyed {condition_id!r} but declares id {condition.condition_id!r}"
            )
        if condition_id not in rules:
            problems.append(f"condition {condition_id!r} has no clinical_validation_rules entry")
        if condition.min_severity not in SEVERITY_ORDER:
            problems.append(
                f"condition {condition_id!r} has unknown min_severity "
                f"{condition.min_severity!r}"
            )
        if condition.severity_floor not in SEVERITY_ORDER:
            problems.append(
                f"condition {condition_id!r} has unknown severity_floor "
                f"{condition.severity_floor!r}"
            )
        if condition.urgency not in URGENCY_LEVELS:
            problems.append(f"condition {condition_id!r} has unknown urgency {condition.urgency!r}")
        for other in condition.supersedes:
            if other not in rules:
                problems.append(f"condition {condition_id!r} supersedes unknown condition {other!r}")
        for rec in condition.recommendations:
            if rec.recommendation_type not in RECOMMENDATION_TYPES:
                problems.append(
                    f"{condition_id}: recommendation {rec.recommendation_id!r} has unknown "
                    f"type {rec.recommendation_type!r}"
                )
    for rule_id in rules:
        if rule_id not in conditions:
            problems.append(f"clinical_validation_rules entry {rule_id!r} has no ClinicalCondition")
    return problems


__all__ = [
    "SEVERITY_ORDER",
    "RECOMMENDATION_TYPES",
    "URGENCY_LEVELS",
    "Recommendation",
    "BiomarkerInterpretation",
    "ClinicalCondition",
    "worst_severity",
    "validate_interpretations",
    "validate_conditions",
]
