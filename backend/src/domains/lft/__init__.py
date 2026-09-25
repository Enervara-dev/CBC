"""
LFT domain — Liver Function Test.

Bundles all liver-panel knowledge into one ``DOMAIN`` config:
  * ``biomarkers``        — codes, aliases, LOINC, graph-name bridge, required panel
  * ``features``          — Layer-3 feature definitions
  * ``validation``        — Layer-5 validation rules
  * ``reference_ranges``  — reference-range seed data (Serum, sex/age-stratified)
  * ``units``             — unit conversions + data-quality limits (Layer 2)
  * ``conditions``        — clinical interpretation + recommendations (Layer 4)

Panel contents: ALT, AST, ALP, GGT, total/direct/indirect bilirubin, total
protein, albumin, globulin, and the A/G ratio.
"""

from __future__ import annotations

from domains.base import DomainConfig
from domains.lft.biomarkers import (
    REQUIRED_BIOMARKERS,
    NAME_TO_CODE,
    CODE_TO_NAME,
    CODE_TO_LOINC,
    BIOMARKER_LOOKUP,
    FACT_TO_BIOMARKER,
    CODE_TO_GRAPH_NAMES,
)
from domains.lft.features import FEATURE_REGISTRY
from domains.lft.validation import load_validation_rules
from domains.lft.reference_ranges import reference_range_rows
from domains.lft.units import load_unit_rules
from domains.lft.conditions import (
    load_biomarker_interpretations,
    load_clinical_conditions,
)

DOMAIN = DomainConfig(
    key="lft",
    name="Liver Function Test",
    required_biomarkers=REQUIRED_BIOMARKERS,
    name_to_code=NAME_TO_CODE,
    code_to_name=CODE_TO_NAME,
    code_to_loinc=CODE_TO_LOINC,
    biomarker_lookup=BIOMARKER_LOOKUP,
    fact_to_biomarker=FACT_TO_BIOMARKER,
    code_to_graph_names=CODE_TO_GRAPH_NAMES,
    feature_registry=FEATURE_REGISTRY,
    validation_rules=load_validation_rules,
    reference_range_rows=reference_range_rows,
    unit_rules=load_unit_rules,
    clinical_conditions=load_clinical_conditions,
    biomarker_interpretations=load_biomarker_interpretations,
    metadata={"specimen_type": "Serum"},
)

__all__ = ["DOMAIN"]
