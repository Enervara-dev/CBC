"""
Confidence validation service (Layer 5).

Applies clinical validation rules, impossibility checks, and confidence
calibration to the Layer 4 findings, then assigns clinical urgency / escalation.
This package owns the validation *rules* (data-driven) separate from the engine
that applies them.
"""
