"""
Orchestration — chains the analysis layers into a single pipeline.

`CBCOrchestrator` runs Layer 2 (normalization) → Layer 3 (features) → Layer 4
(graph reasoning) → Layer 5 (validation) for a CBC panel, threading a nested
audit trail and isolating per-layer failures.
"""
