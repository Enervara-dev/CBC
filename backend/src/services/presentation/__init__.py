"""
Layer 6 — LLM Presentation Engine.

Turns validated Layer 5 findings into human-readable patient and clinician
reports. The LLM is a **presentation layer only** — it rephrases/explains
validated findings and never creates, modifies, or invents findings,
diagnoses, confidence values, lab values, recommendations, or guideline
references. All structured exports (JSON / HL7 / CSV / PDF metadata) are
generated **deterministically without the LLM**.
"""
