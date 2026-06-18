"""
Stage 7 — Template Summary Generator
Pure template-driven output. No LLM. Driven entirely by rule-based output.
"""

from typing import List, Dict, Any, Optional


def generate(
    flagged_markers: List[Dict[str, Any]],
    conditions: Dict[str, float],
    top_condition: str,
    severity: str,
    top_features: List[str],
    marker_dict: Dict[str, Optional[float]],
    config,
) -> Dict[str, Any]:
    T = config.SUMMARY_TEMPLATES

    # Marker summaries — only abnormal ones
    flagged_summaries = []
    for m in flagged_markers:
        flag = m.get("flag", "UNKNOWN")
        if flag in ("NORMAL", "UNKNOWN"):
            continue
        name = m["parameter"]
        value = m.get("value")
        unit = m.get("unit", "")
        ref_low = m.get("ref_low", "")
        ref_high = m.get("ref_high", "")

        if flag == "CRITICAL_HIGH":
            flagged_summaries.append(T["marker_critical_high"].format(
                name=name, value=value, unit=unit))
        elif flag == "CRITICAL_LOW":
            flagged_summaries.append(T["marker_critical_low"].format(
                name=name, value=value, unit=unit))
        elif flag == "HIGH":
            flagged_summaries.append(T["marker_high"].format(
                name=name, value=value, unit=unit, ref_low=ref_low, ref_high=ref_high))
        elif flag == "LOW":
            flagged_summaries.append(T["marker_low"].format(
                name=name, value=value, unit=unit, ref_low=ref_low, ref_high=ref_high))

    # Condition findings
    condition_findings = []
    if top_condition == "Normal" or severity == "Normal":
        condition_findings.append(T["normal_finding"])
    else:
        for condition, prob in sorted(conditions.items(), key=lambda x: -x[1]):
            if condition == "Normal" or prob < 0.35:
                continue
            feat_str = ", ".join(top_features) if top_features else "N/A"
            condition_findings.append(T["condition_finding"].format(
                severity=severity,
                condition=condition,
                probability=prob,
                top_features=feat_str,
            ))

    # AST:ALT ratio note
    ast_alt_note = None
    alt = marker_dict.get("ALT")
    ast = marker_dict.get("AST")
    if alt and ast and alt > 0 and "ast_alt_note" in T:
        ratio = ast / alt
        interpretation = (
            T["ast_alt_alcoholic"] if ratio >= 2.0 else T["ast_alt_nonalcoholic"]
        )
        ast_alt_note = T["ast_alt_note"].format(ratio=ratio, interpretation=interpretation)

    # Recommendation
    rec_key = {
        "Normal":   "recommendation_normal",
        "Mild":     "recommendation_mild",
        "Moderate": "recommendation_moderate",
        "Severe":   "recommendation_severe",
    }.get(severity, "recommendation_normal")
    recommendation = T.get(rec_key, "Please consult a qualified physician.")

    # Overall status
    if severity == "Normal":
        overall_status = "All liver function parameters within normal limits."
    else:
        overall_status = f"{severity} hepatic abnormality detected — {top_condition}."

    # Full text
    parts = [T.get("header", ""), "", overall_status, ""]
    if flagged_summaries:
        parts += flagged_summaries + [""]
    parts += condition_findings
    if ast_alt_note:
        parts += ["", ast_alt_note]
    parts += ["", recommendation]

    return {
        "overall_status":        overall_status,
        "flagged_markers_summary": flagged_summaries,
        "condition_findings":    condition_findings,
        "ast_alt_note":          ast_alt_note,
        "recommendation":        recommendation,
        "full_text":             "\n".join(parts).strip(),
    }
