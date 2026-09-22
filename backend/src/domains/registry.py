"""
Domain registry — the one place that knows which specialties exist.

The pipeline asks the registry for a ``DomainConfig`` by key; it never imports a
specialty directly. Registering a new specialty is a single line here.

    from domains.registry import get_domain
    cbc = get_domain("cbc")

Merged views
------------
Several pipeline stages are handed *one* table and must serve every registered
panel at once — Layer 1 resolves an OCR name without knowing which panel the
report is (a report may even mix panels), and Layer 2's converter/quality checker
are static services. Those stages read the ``merged_*`` accessors below, which
union the per-domain tables and fail loudly on a key that two panels define
differently (a silent overwrite would misread one panel's results as another's).

Panel detection
---------------
:func:`detect_panels` maps a set of biomarker codes back to the panels they
belong to, so the orchestrator can enforce *that* panel's required-biomarker set
instead of always demanding CBC's.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from domains.base import (
    UNIT_RULE_KEYS,
    DomainConfig,
    DomainConsistencyError,
    unit_rule_tables,
    validate_domain,
)
from domains.cbc import DOMAIN as CBC_DOMAIN
from domains.lft import DOMAIN as LFT_DOMAIN
from domains.lipid import DOMAIN as LIPID_DOMAIN

# Register every available specialty here (key → config).
_REGISTRY: Dict[str, DomainConfig] = {
    CBC_DOMAIN.key: CBC_DOMAIN,
    LFT_DOMAIN.key: LFT_DOMAIN,
    LIPID_DOMAIN.key: LIPID_DOMAIN,
    # To add a specialty: build domains/<name>/ (copy domains/_template), then add
    #   from domains.<name> import DOMAIN as <NAME>_DOMAIN
    # at the top and `<NAME>_DOMAIN.key: <NAME>_DOMAIN,` to this dict.
}

# Fail fast on table drift: a registered domain whose lookup tables disagree
# would silently drop biomarkers/facts mid-pipeline. Surface it here instead.
for _domain in _REGISTRY.values():
    validate_domain(_domain)

DEFAULT_DOMAIN = "cbc"


def get_domain(key: str = DEFAULT_DOMAIN) -> DomainConfig:
    """Return the domain config for ``key`` (case-insensitive)."""
    try:
        return _REGISTRY[key.lower()]
    except KeyError:
        raise KeyError(
            f"Unknown domain {key!r}. Available: {', '.join(sorted(_REGISTRY))}"
        ) from None


def available_domains() -> List[str]:
    """Sorted list of registered domain keys."""
    return sorted(_REGISTRY)


def all_domains() -> List[DomainConfig]:
    """Every registered domain config, ordered by key."""
    return [_REGISTRY[key] for key in available_domains()]


# ─────────────────────────────────────────────────────────────────────────────
# Merged (cross-panel) views
# ─────────────────────────────────────────────────────────────────────────────
def _merge(
    label: str,
    tables: Iterable[Tuple[str, Mapping[str, Any]]],
) -> Dict[str, Any]:
    """
    Union per-domain tables, raising if two panels map one key to different values.

    A silent overwrite here would mean one panel's alias resolving to another
    panel's biomarker — the kind of drift ``check_domain`` catches *inside* a
    domain, applied *between* them.
    """
    merged: Dict[str, Any] = {}
    owner: Dict[str, str] = {}
    for domain_key, table in tables:
        for key, value in table.items():
            if key in merged and merged[key] != value:
                raise DomainConsistencyError(
                    f"Conflicting {label} for {key!r}: {owner[key]} says {merged[key]!r}, "
                    f"{domain_key} says {value!r}. Rename one of them."
                )
            merged[key] = value
            owner.setdefault(key, domain_key)
    return merged


def merged_name_to_code() -> Dict[str, str]:
    """Every panel's extracted-name/synonym → canonical code (Layer 2)."""
    return _merge("name_to_code alias", ((d.key, d.name_to_code) for d in all_domains()))


def merged_code_to_name() -> Dict[str, str]:
    """Every panel's canonical code → canonical name (Layers 2, 3, 5)."""
    return _merge("code_to_name entry", ((d.key, d.code_to_name) for d in all_domains()))


def merged_code_to_loinc() -> Dict[str, str]:
    """Every panel's canonical code → LOINC code (Layers 2, 4)."""
    return _merge("code_to_loinc entry", ((d.key, d.code_to_loinc) for d in all_domains()))


def merged_biomarker_lookup() -> Dict[str, str]:
    """Every panel's OCR alias → canonical code (Layer 1 adapter)."""
    return _merge("biomarker_lookup alias", ((d.key, d.biomarker_lookup) for d in all_domains()))


def merged_fact_to_biomarker() -> Dict[str, Any]:
    """Every panel's fact id → biomarker code(s) (Layer 4 bridge)."""
    return _merge("fact_to_biomarker entry", ((d.key, d.fact_to_biomarker) for d in all_domains()))


def merged_code_to_graph_names() -> Dict[str, List[str]]:
    """Every panel's canonical code → graph Biomarker.name candidates (Layer 4)."""
    return _merge(
        "code_to_graph_names entry", ((d.key, d.code_to_graph_names) for d in all_domains())
    )


def merged_feature_registry() -> Dict[str, Any]:
    """Every panel's Layer-3 feature definitions, keyed by feature id."""
    return _merge("feature definition", ((d.key, d.feature_registry) for d in all_domains()))


def merged_clinical_conditions() -> Dict[str, Any]:
    """Every panel's multi-marker clinical conditions, keyed by condition id."""
    return _merge(
        "clinical condition", ((d.key, d.clinical_conditions() or {}) for d in all_domains())
    )


def merged_biomarker_interpretations() -> Dict[str, Any]:
    """Every panel's per-biomarker interpretations, keyed by canonical code."""
    return _merge(
        "biomarker interpretation",
        ((d.key, d.biomarker_interpretations() or {}) for d in all_domains()),
    )


def merged_unit_rules() -> Dict[str, Dict[str, Any]]:
    """
    Every panel's Layer-2 unit + data-quality tables, keyed by canonical name.

    Returns one dict per table in ``UNIT_RULE_KEYS``.
    """
    tables = {domain.key: unit_rule_tables(domain) for domain in all_domains()}
    return {
        table_name: _merge(
            f"unit_rules[{table_name}] entry",
            ((key, tables[key][table_name]) for key in sorted(tables)),
        )
        for table_name in UNIT_RULE_KEYS
    }


# Rule sets that describe *findings* and merge cleanly across panels (their keys
# are biomarker names / condition ids, which are panel-unique).
_SHARED_RULE_SETS: Tuple[str, ...] = (
    "severity_thresholds",
    "clinical_validation_rules",
    "impossible_conditions",
)
# Rule sets keyed by severity band, so every panel defines the same keys with
# legitimately different values (CBC escalates to haematology, LFT to hepatology).
# These are exposed per panel instead of being merged into a single answer.
_PANEL_SCOPED_RULE_SETS: Tuple[str, ...] = (
    "confidence_calibration",
    "urgency_flags",
)


def merged_validation_rules() -> Dict[str, Any]:
    """
    Every panel's Layer-5 rule sets in the bundle shape the engine expects.

    The finding-keyed rule sets are strict-merged (a genuine disagreement raises
    rather than letting registration order decide). The severity-keyed ones are
    panel-scoped: the bundle carries ``DEFAULT_DOMAIN``'s table under the plain
    key — so a caller that knows nothing about panels still gets a sane answer —
    plus every panel's table under ``<rule_set>_by_domain`` for callers that can
    route by biomarker (see ``ConfidenceValidationEngine._urgency_entry``).
    """
    bundles = [(domain.key, domain.validation_rules()) for domain in all_domains()]
    merged: Dict[str, Any] = {
        rule_set: _merge(
            f"{rule_set} entry",
            ((key, bundle.get(rule_set, {})) for key, bundle in bundles),
        )
        for rule_set in _SHARED_RULE_SETS
    }
    for rule_set in _PANEL_SCOPED_RULE_SETS:
        by_domain = {key: bundle.get(rule_set, {}) for key, bundle in bundles}
        merged[rule_set] = by_domain.get(DEFAULT_DOMAIN, {})
        merged[f"{rule_set}_by_domain"] = by_domain
    return merged


def domain_for_code(code: str) -> str | None:
    """Return the key of the domain that owns ``code``, or ``None`` if unknown."""
    upper = code.strip().upper()
    for domain in all_domains():
        if upper in domain.code_to_name:
            return domain.key
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Panel detection
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class PanelDetection:
    """
    Which panel(s) a set of biomarker codes represents.

    Attributes
    ----------
    matched : Dict[str, List[str]]
        domain key → the submitted codes belonging to it (sorted).
    primary : List[str]
        The domain key(s) with the most matched codes — the panel(s) actually
        being reported, and therefore the ones whose ``required_biomarkers`` the
        orchestrator enforces. A mixed report yields the dominant panel; a tie
        yields every tied panel.
    unknown : List[str]
        Submitted codes no registered domain recognises.
    """

    matched: Dict[str, List[str]]
    primary: List[str]
    unknown: List[str]


def detect_panels(codes: Iterable[str]) -> PanelDetection:
    """
    Map biomarker codes back to the panels they belong to.

    Codes are matched case-insensitively against each domain's ``code_to_name``.
    """
    submitted = [c.strip().upper() for c in codes if c and c.strip()]
    matched: Dict[str, List[str]] = {}
    unknown: List[str] = []

    for code in submitted:
        key = domain_for_code(code)
        if key is None:
            unknown.append(code)
        else:
            matched.setdefault(key, []).append(code)

    counts = Counter({key: len(found) for key, found in matched.items()})
    top = max(counts.values()) if counts else 0
    primary = sorted(key for key, count in counts.items() if count == top and top > 0)

    return PanelDetection(
        matched={key: sorted(found) for key, found in sorted(matched.items())},
        primary=primary,
        unknown=sorted(set(unknown)),
    )


def _validate_cross_panel_tables() -> None:
    """
    Build every merged view once at import, so a cross-panel conflict fails here.

    Individually each domain can be perfectly consistent while still clashing with
    another — the same alias pointing at two biomarkers, or two panels reusing a
    canonical name. Both would silently misread one panel's results as another's,
    so registration is the place to find out.
    """
    for merge in (
        merged_name_to_code, merged_code_to_name, merged_code_to_loinc,
        merged_biomarker_lookup, merged_fact_to_biomarker, merged_code_to_graph_names,
        merged_feature_registry, merged_unit_rules, merged_validation_rules,
        merged_clinical_conditions, merged_biomarker_interpretations,
    ):
        merge()


_validate_cross_panel_tables()


__all__ = [
    "get_domain",
    "available_domains",
    "all_domains",
    "DEFAULT_DOMAIN",
    "PanelDetection",
    "detect_panels",
    "domain_for_code",
    "merged_name_to_code",
    "merged_code_to_name",
    "merged_code_to_loinc",
    "merged_biomarker_lookup",
    "merged_fact_to_biomarker",
    "merged_code_to_graph_names",
    "merged_feature_registry",
    "merged_unit_rules",
    "merged_validation_rules",
    "merged_clinical_conditions",
    "merged_biomarker_interpretations",
]
