"""
Tests for the domain layer's cross-layer consistency guard.

The pipeline reads each panel through a single ``DomainConfig``. If its lookup
tables disagree (a code referenced in one table but missing from the canonical
``code_to_name`` vocabulary), a biomarker recognised by one layer is silently
dropped by the next. ``check_domain`` / ``validate_domain`` turn that drift into
a loud, testable failure; the registry runs it at import time.
"""

import dataclasses

import pytest

from domains.base import DomainConfig, check_domain, validate_domain, DomainConsistencyError
from domains.cbc import DOMAIN as CBC_DOMAIN
from domains.registry import available_domains, get_domain


class TestRegisteredDomains:
    def test_cbc_is_registered(self):
        assert "cbc" in available_domains()
        assert get_domain("cbc") is CBC_DOMAIN

    def test_cbc_domain_is_consistent(self):
        assert check_domain(CBC_DOMAIN) == []

    def test_every_registered_domain_is_consistent(self):
        for key in available_domains():
            assert check_domain(get_domain(key)) == [], f"{key} has table drift"


class TestConsistencyChecks:
    def _mutate(self, **changes) -> DomainConfig:
        return dataclasses.replace(CBC_DOMAIN, **changes)

    def test_alias_to_unknown_code_is_flagged(self):
        bad = self._mutate(name_to_code={**CBC_DOMAIN.name_to_code, "ferritin": "FERR"})
        problems = check_domain(bad)
        assert any("FERR" in p for p in problems)

    def test_loinc_for_unknown_code_is_flagged(self):
        bad = self._mutate(code_to_loinc={**CBC_DOMAIN.code_to_loinc, "FOO": "1-1"})
        assert any("FOO" in p for p in check_domain(bad))

    def test_required_biomarker_without_range_is_flagged(self):
        # A canonical code with no reference range can never normalize if required.
        bad = self._mutate(
            code_to_name={**CBC_DOMAIN.code_to_name, "RETIC": "reticulocytes"},
            required_biomarkers=CBC_DOMAIN.required_biomarkers + ("RETIC",),
        )
        problems = check_domain(bad)
        assert any("reference range" in p for p in problems)

    def test_validate_domain_raises_on_drift(self):
        bad = self._mutate(biomarker_lookup={**CBC_DOMAIN.biomarker_lookup, "mono": "MONO"})
        with pytest.raises(DomainConsistencyError):
            validate_domain(bad)

    def test_validate_domain_passes_for_cbc(self):
        validate_domain(CBC_DOMAIN)  # should not raise

    def test_unit_rule_for_unknown_name_is_flagged(self):
        """Unit/quality tables are keyed by canonical NAME; a typo there silently
        disables conversion and the panic-value check for that marker."""
        bad = self._mutate(
            unit_rules=lambda: {"conversion_factors": {"haemoglobin": {"g/dL": 1.0}},
                                "standard_units": {"haemoglobin": "g/dL"}},
        )
        problems = check_domain(bad)
        assert any("haemoglobin" in p for p in problems)

    def test_conversion_without_standard_unit_is_flagged(self):
        bad = self._mutate(
            unit_rules=lambda: {"conversion_factors": {"hemoglobin": {"g/dL": 1.0}}},
        )
        assert any("without a standard unit" in p for p in check_domain(bad))

    def test_standard_unit_must_have_factor_one(self):
        bad = self._mutate(
            unit_rules=lambda: {"conversion_factors": {"hemoglobin": {"g/L": 0.1}},
                                "standard_units": {"hemoglobin": "g/L"}},
        )
        assert any("must have factor 1.0" in p for p in check_domain(bad))


class TestDifferentialMarkerWiring:
    """NEUT/LYMPH must be fully wired (regression: they were seeded + featured but
    absent from the name tables, so they were dropped at Layer 2)."""

    @pytest.mark.parametrize("code", ["NEUT", "LYMPH"])
    def test_marker_is_normalizable_and_graphable(self, code):
        assert code in CBC_DOMAIN.code_to_name
        assert code in CBC_DOMAIN.code_to_loinc
        assert code in CBC_DOMAIN.code_to_graph_names
        assert code in {v for v in CBC_DOMAIN.name_to_code.values()}

    @pytest.mark.parametrize("dropped", ["MONO", "EOS", "BASO", "MPV"])
    def test_unsupported_markers_are_not_advertised(self, dropped):
        # Removed from the OCR/graph tables since they have no range/feature backing.
        assert dropped not in CBC_DOMAIN.biomarker_lookup.values()
        assert dropped not in CBC_DOMAIN.code_to_graph_names
