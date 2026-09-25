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


# The five tables a domain's ``units.py`` provides (all keyed by canonical name).
UNIT_RULE_KEYS: Tuple[str, ...] = (
    "conversion_factors",   # name -> {unit: factor to the standard unit}
    "standard_units",       # name -> standard unit (the factor-1.0 one)
    "absolute_limits",      # name -> {"min": x, "max": y}  (physiologically possible)
    "critical_values",      # name -> {"low": x, "high": y} (panic values)
    "display_names",        # name -> human label for quality messages
)


def _no_unit_rules() -> Dict[str, Any]:
    """Default for domains that declare no unit / quality tables."""
    return {key: {} for key in UNIT_RULE_KEYS}


def _no_clinical_data() -> Dict[str, Any]:
    """Default for domains with no clinical interpretation catalogue yet."""
    return {}


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
    clinical_conditions / biomarker_interpretations :
        Callables returning the Layer-4 interpretation catalogue — what an
        abnormal result *means* and what to do about it (see
        ``<domain>/conditions.py`` and ``domains/clinical_types.py``). Empty for a
        panel that has not authored them yet; the reasoner simply reports the
        abnormality without narrative.
    unit_rules :
        Callable returning this panel's unit-conversion and data-quality tables
        (see ``<domain>/units.py`` and :func:`unit_rule_keys`). Keyed by the
        canonical *name* (``code_to_name`` values), which is what Layer 2's
        ``UnitConverter`` / ``DataQualityChecker`` are keyed by.
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
    unit_rules: Callable[[], Dict[str, Any]] = _no_unit_rules
    clinical_conditions: Callable[[], Dict[str, Any]] = _no_clinical_data
    biomarker_interpretations: Callable[[], Dict[str, Any]] = _no_clinical_data
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

    # Unit / quality tables are keyed by canonical NAME (not code): a typo there
    # silently disables unit conversion and the panic-value check for that marker.
    known_names = set(config.code_to_name.values())
    tables = unit_rule_tables(config)
    for table_name in UNIT_RULE_KEYS:
        unknown = sorted(set(tables[table_name]) - known_names)
        if unknown:
            problems.append(
                f"unit_rules[{table_name!r}] reference unknown name(s) {unknown} "
                "(not a code_to_name value)"
            )
    # Clinical interpretation tables must reference real codes and real rules, or
    # a finding the reasoner tries to explain silently loses its narrative.
    from domains.clinical_types import validate_conditions, validate_interpretations

    interpretations = config.biomarker_interpretations() or {}
    if interpretations:
        problems.extend(validate_interpretations(interpretations, config.code_to_name))
    conditions = config.clinical_conditions() or {}
    if conditions:
        rules = (config.validation_rules() or {}).get("clinical_validation_rules", {})
        problems.extend(validate_conditions(conditions, rules))

    # Every convertible marker needs a standard unit, or conversion has no target.
    without_standard = sorted(set(tables["conversion_factors"]) - set(tables["standard_units"]))
    if without_standard:
        problems.append(f"unit_rules: conversion_factors without a standard unit: {without_standard}")
    for name, unit in tables["standard_units"].items():
        factors = tables["conversion_factors"].get(name, {})
        if factors and factors.get(unit) != 1.0:
            problems.append(
                f"unit_rules: standard unit {unit!r} for {name!r} must have factor 1.0"
            )

    # Required biomarkers must also have a reference range, or they can't normalize.
    missing_ranges = sorted(set(config.required_biomarkers) - {c for c in range_codes if c})
    if missing_ranges:
        problems.append(f"required_biomarkers without a reference range: {missing_ranges}")

    return problems


def unit_rule_tables(config: DomainConfig) -> Dict[str, Dict[str, Any]]:
    """Return ``config``'s unit/quality tables with every expected key present."""
    provided = config.unit_rules() or {}
    return {key: dict(provided.get(key) or {}) for key in UNIT_RULE_KEYS}


def validate_domain(config: DomainConfig) -> None:
    """Raise :class:`DomainConsistencyError` if ``config`` has any violations."""
    problems = check_domain(config)
    if problems:
        joined = "\n  - ".join(problems)
        raise DomainConsistencyError(
            f"Domain {config.key!r} has inconsistent lookup tables:\n  - {joined}"
        )
