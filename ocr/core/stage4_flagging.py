"""
Stage 4 — Flagging Engine
Compares each parsed value against reference ranges and critical thresholds.
"""

from typing import List, Dict, Any


FLAGS = ["NORMAL", "LOW", "HIGH", "CRITICAL_LOW", "CRITICAL_HIGH", "UNKNOWN"]


def flag(parsed_markers: List[Dict[str, Any]], config) -> List[Dict[str, Any]]:
    """
    Augments each marker dict with a "flag" field.
    Also adds "is_critical" bool.
    """
    flagged = []
    for marker in parsed_markers:
        m = dict(marker)
        value = m.get("value")
        param = m.get("parameter")

        if value is None:
            m["flag"] = "UNKNOWN"
            m["is_critical"] = False
            flagged.append(m)
            continue

        # Critical check takes precedence
        crit = config.CRITICAL_THRESHOLDS.get(param)
        if crit:
            crit_low, crit_high = crit
            if crit_low is not None and value < crit_low:
                m["flag"] = "CRITICAL_LOW"
                m["is_critical"] = True
                flagged.append(m)
                continue
            if crit_high is not None and value > crit_high:
                m["flag"] = "CRITICAL_HIGH"
                m["is_critical"] = True
                flagged.append(m)
                continue

        # Normal range check
        ref_low = m.get("ref_low")
        ref_high = m.get("ref_high")

        if ref_low is None or ref_high is None:
            # Fall back to config
            if param in config.REFERENCE_RANGES:
                ref_low, ref_high, _ = config.REFERENCE_RANGES[param]

        m["is_critical"] = False
        if ref_low is not None and value < ref_low:
            m["flag"] = "LOW"
        elif ref_high is not None and value > ref_high:
            m["flag"] = "HIGH"
        else:
            m["flag"] = "NORMAL"

        flagged.append(m)

    return flagged


def build_marker_dict(flagged_markers: List[Dict[str, Any]]) -> Dict[str, float]:
    """Build {parameter: value} dict (None for missing) for the rule-based detector."""
    return {m["parameter"]: m.get("value") for m in flagged_markers}
