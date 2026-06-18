"""
Stage 5 — Rule-Based Condition Detection
No ML model. Conditions are scored entirely from clinical rules defined in config.
Each rule examines marker values and assigns a probability to each condition.
"""

from typing import Dict, List, Any, Optional


def detect(marker_dict: Dict[str, Optional[float]], config) -> Dict[str, Any]:
    """
    Run all clinical scoring rules from config and return condition probabilities.

    Returns:
    {
        "conditions":    {condition: probability},
        "top_condition": str,
        "severity":      str,
        "top_features":  [str],   # markers that are abnormal — used in summary
    }
    """
    # Start with all conditions at 0
    probs = {c: 0.0 for c in config.CONDITIONS}

    # Run every rule in sequence — each one can read and modify probs
    for rule_fn in config.CLINICAL_RULES:
        probs = rule_fn(marker_dict, probs)

    # Clamp all to [0, 1]
    probs = {k: max(0.0, min(1.0, v)) for k, v in probs.items()}

    top_condition, severity = _grade(probs, config)
    top_features = _top_abnormal_markers(marker_dict, config)

    return {
        "conditions":    probs,
        "top_condition": top_condition,
        "severity":      severity,
        "top_features":  top_features,
    }


def _grade(probs: Dict[str, float], config) -> tuple:
    """Pick the top non-Normal condition and assign severity."""
    non_normal = {k: v for k, v in probs.items() if k != "Normal" and v >= 0.35}

    if not non_normal:
        return "Normal", "Normal"

    top_condition = max(non_normal, key=non_normal.get)
    top_prob = non_normal[top_condition]

    severity = "Normal"
    for threshold, label in config.SEVERITY_BANDS:
        if top_prob >= threshold:
            severity = label
            break

    return top_condition, severity


def _top_abnormal_markers(
    marker_dict: Dict[str, Optional[float]], config, n: int = 3
) -> List[str]:
    """Return the n most abnormal markers (largest % deviation from ref range)."""
    deviations = []
    for marker, value in marker_dict.items():
        if value is None or marker not in config.REFERENCE_RANGES:
            continue
        low, high, _ = config.REFERENCE_RANGES[marker]
        mid = (low + high) / 2
        span = (high - low) / 2 if high != low else 1
        deviation = abs(value - mid) / span
        if deviation > 1.0:   # only include if actually outside range
            deviations.append((marker, deviation))

    deviations.sort(key=lambda x: -x[1])
    return [m for m, _ in deviations[:n]]
