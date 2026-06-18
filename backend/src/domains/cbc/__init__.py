"""
CBC domain — Complete Blood Count.

Bundles all CBC-specific knowledge into one ``DOMAIN`` config:
  * ``biomarkers``        — codes, aliases, LOINC, graph-name bridge, required panel
  * ``features``          — Layer-3 feature definitions
  * ``validation``        — Layer-5 validation rules
  * ``reference_ranges``  — reference-range seed data

To add a new specialty, copy this folder, edit the data modules, and register the
new ``DOMAIN`` in ``domains/registry.py`` (see ``domains/_template`` and the README).
"""

from __future__ import annotations

from domains.base import DomainConfig
from domains.cbc.biomarkers import (
    REQUIRED_BIOMARKERS,
    NAME_TO_CODE,
    CODE_TO_NAME,
    CODE_TO_LOINC,
    BIOMARKER_LOOKUP,
    FACT_TO_BIOMARKER,
    CODE_TO_GRAPH_NAMES,
)
from domains.cbc.features import FEATURE_REGISTRY
from domains.cbc.validation import load_validation_rules
from domains.cbc.reference_ranges import reference_range_rows

DOMAIN = DomainConfig(
    key="cbc",
    name="Complete Blood Count",
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
)

__all__ = ["DOMAIN"]
