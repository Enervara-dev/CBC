"""
Domain abstraction — the contract every specialty (CBC, LFT, Lipid, …) fulfils.

A *domain* bundles all the panel-specific knowledge the generic pipeline needs:
the biomarker vocabulary, LOINC codes, Layer-3 feature definitions, Layer-5
validation rules, and the reference-range seed data. The pipeline stays generic;
adding a specialty means adding a folder that exposes one ``DomainConfig`` named
``DOMAIN`` and registering it (see ``domains/registry.py`` and ``domains/_template``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple, Union


@dataclass(frozen=True)
class DomainConfig:
    """Everything panel-specific, in one object.

    Attributes
    ----------
    key, name :
        Machine key (e.g. ``"cbc"``) and human label (e.g. ``"Complete Blood Count"``).
    required_biomarkers :
        Codes that must be present to run an analysis.
    name_to_code / code_to_name / code_to_loinc / biomarker_lookup :
        Name-resolution and LOINC vocabulary (see ``<domain>/biomarkers.py``).
    fact_to_biomarker / code_to_graph_names :
        Layer-4 bridge between feature facts, codes, and graph node names.
    feature_registry :
        Layer-3 feature definitions keyed by feature id.
    validation_rules :
        Callable returning the Layer-5 rule bundle (lazy so import stays cheap).
    reference_range_rows :
        Callable returning the reference-range seed rows for the database.
    """

    key: str
    name: str
    required_biomarkers: Tuple[str, ...]
    name_to_code: Dict[str, str]
    code_to_name: Dict[str, str]
    code_to_loinc: Dict[str, str]
    biomarker_lookup: Dict[str, str]
    fact_to_biomarker: Dict[str, Union[str, List[str]]]
    code_to_graph_names: Dict[str, List[str]]
    feature_registry: Dict[str, Any]
    validation_rules: Callable[[], Dict[str, Any]]
    reference_range_rows: Callable[[], List[Dict[str, Any]]]
    metadata: Dict[str, Any] = field(default_factory=dict)
