"""
Lipid domain — Lipid Profile.

Bundles all lipid-panel knowledge into one ``DOMAIN`` config:
  * ``biomarkers``        — codes, aliases, LOINC, graph-name bridge, required panel
  * ``features``          — Layer-3 feature definitions
  * ``validation``        — Layer-5 validation rules
  * ``reference_ranges``  — desirable cut-points as seed rows (Serum)
  * ``units``             — unit conversions + data-quality limits (Layer 2)
  * ``conditions``        — clinical interpretation + recommendations (Layer 4)

Panel contents: total cholesterol, LDL, HDL, triglycerides, VLDL, non-HDL
cholesterol, and the total-cholesterol/HDL ratio.
"""

from __future__ import annotations

from domains.base import DomainConfig
from domains.lipid.biomarkers import (
    REQUIRED_BIOMARKERS,
    NAME_TO_CODE,
    CODE_TO_NAME,
    CODE_TO_LOINC,
    BIOMARKER_LOOKUP,
    FACT_TO_BIOMARKER,
    CODE_TO_GRAPH_NAMES,
)
from domains.lipid.features import FEATURE_REGISTRY
from domains.lipid.validation import load_validation_rules
from domains.lipid.reference_ranges import reference_range_rows
from domains.lipid.units import load_unit_rules
from domains.lipid.conditions import (
    load_biomarker_interpretations,
    load_clinical_conditions,
)

DOMAIN = DomainConfig(
    key="lipid",
    name="Lipid Profile",
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
    metadata={"specimen_type": "Serum", "fasting_required": True},
)

__all__ = ["DOMAIN"]
