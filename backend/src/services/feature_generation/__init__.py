"""
Feature generation service (Layer 3).

Turns normalized biomarkers (Layer 2 output) into clinical features — binary
abnormality flags, severity grades, computed ratios, and disease patterns — that
the downstream reasoning layer consumes. This package owns the *definitions* of
those features (a knowledge-graph-style library), kept separate from the
evaluator that applies them to a patient's values.
"""
