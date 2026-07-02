"""
Domain registry — the one place that knows which specialties exist.

The pipeline asks the registry for a ``DomainConfig`` by key; it never imports a
specialty directly. Registering a new specialty is a single line here.

    from domains.registry import get_domain
    cbc = get_domain("cbc")
"""

from __future__ import annotations

from typing import Dict, List

from domains.base import DomainConfig, validate_domain
from domains.cbc import DOMAIN as CBC_DOMAIN

# Register every available specialty here (key → config).
_REGISTRY: Dict[str, DomainConfig] = {
    CBC_DOMAIN.key: CBC_DOMAIN,
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


__all__ = ["get_domain", "available_domains", "DEFAULT_DOMAIN"]
