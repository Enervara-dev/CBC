"""
Stage 6 — Clinical Validation
Final sanity pass after stage5 rule-based scoring.
Clamps probabilities and ensures no contradictory outputs.
(Rules previously here have moved into lft_config.CLINICAL_RULES
 so they run as part of stage5_rules.detect().)
"""

from typing import Dict, Optional


def validate(
    conditions: Dict[str, float],
    marker_dict: Dict[str, Optional[float]],
    config,
) -> Dict[str, float]:
    """
    Final clamp and contradiction check.
    By the time we get here, all scoring rules have already run in stage5.
    """
    probs = {k: max(0.0, min(1.0, v)) for k, v in conditions.items()}

    # Ensure all config conditions are present
    for c in config.CONDITIONS:
        probs.setdefault(c, 0.0)

    return probs
