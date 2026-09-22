"""
Specialty template — copy this folder to add a new panel (LFT, Lipid, …).

Steps
-----
1. Copy ``domains/_template`` → ``domains/<your_panel>`` (e.g. ``domains/lft``).
2. Fill in the five data modules: ``biomarkers.py``, ``features.py``,
   ``validation.py``, ``reference_ranges.py``, ``units.py``.
3. Set ``key`` / ``name`` below and keep this ``DOMAIN`` export.
4. Register it in ``domains/registry.py`` (one line — see that file).

That's the whole contract. The generic pipeline reads everything through the
``DOMAIN`` object, so no pipeline code needs to change.
"""

from __future__ import annotations

from domains.base import DomainConfig
from domains._template.biomarkers import (
    REQUIRED_BIOMARKERS,
    NAME_TO_CODE,
    CODE_TO_NAME,
    CODE_TO_LOINC,
    BIOMARKER_LOOKUP,
    FACT_TO_BIOMARKER,
    CODE_TO_GRAPH_NAMES,
)
from domains._template.features import FEATURE_REGISTRY
from domains._template.validation import load_validation_rules
from domains._template.reference_ranges import reference_range_rows
from domains._template.units import load_unit_rules

DOMAIN = DomainConfig(
    key="template",          # ← change to your panel key, e.g. "lft"
    name="Template Panel",   # ← human-readable name
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
)

__all__ = ["DOMAIN"]
