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


class DomainConsistencyError(ValueError):
    """Raised when a :class:`DomainConfig`'s lookup tables disagree.

    The pipeline drops any biomarker/fact that is referenced in one table but
    absent from the canonical vocabulary, often silently. This error surfaces
    that drift at registration time instead.
    """


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


def _fact_target_codes(fact_to_biomarker: Dict[str, Union[str, List[str]]]) -> List[str]:
    """Flatten the fact→code(s) mapping into the set of referenced codes."""
    codes: List[str] = []
    for target in fact_to_biomarker.values():
        codes.extend(target if isinstance(target, list) else [target])
    return codes


def check_domain(config: DomainConfig) -> List[str]:
    """Return a list of consistency violations for ``config`` (empty == consistent).

    The canonical biomarker vocabulary is ``code_to_name``'s keys. Every other
    table may only *reference* codes in that set — otherwise a biomarker that one
    layer recognises is silently dropped by the next (e.g. a marker with a
    reference range but no canonical name can never normalize). Each check below
    guards one such cross-layer hand-off.
    """
    canonical = set(config.code_to_name)
    problems: List[str] = []

    def _missing(label: str, codes: Any) -> None:
        unknown = sorted({c for c in codes if c and c not in canonical})
        if unknown:
            problems.append(f"{label} reference unknown code(s) {unknown} (not in code_to_name)")

    _missing("name_to_code targets", config.name_to_code.values())
    _missing("biomarker_lookup targets", config.biomarker_lookup.values())
    _missing("code_to_loinc keys", config.code_to_loinc.keys())
    _missing("code_to_graph_names keys", config.code_to_graph_names.keys())
    _missing("fact_to_biomarker targets", _fact_target_codes(config.fact_to_biomarker))
    _missing("required_biomarkers", config.required_biomarkers)
    _missing(
        "feature_registry biomarker_ids",
        [getattr(fd, "biomarker_id", "") for fd in config.feature_registry.values()],
    )

    range_codes = {row.get("biomarker_id") for row in config.reference_range_rows()}
    _missing("reference_range_rows biomarker_ids", range_codes)

    # Required biomarkers must also have a reference range, or they can't normalize.
    missing_ranges = sorted(set(config.required_biomarkers) - {c for c in range_codes if c})
    if missing_ranges:
        problems.append(f"required_biomarkers without a reference range: {missing_ranges}")

    return problems


def validate_domain(config: DomainConfig) -> None:
    """Raise :class:`DomainConsistencyError` if ``config`` has any violations."""
    problems = check_domain(config)
    if problems:
        joined = "\n  - ".join(problems)
        raise DomainConsistencyError(
            f"Domain {config.key!r} has inconsistent lookup tables:\n  - {joined}"
        )
