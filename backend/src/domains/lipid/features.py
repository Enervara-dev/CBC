"""
Lipid-profile Layer-3 feature definitions.

A declarative catalogue: WHAT clinical features exist for a lipid profile and the
medical logic behind each. Layer 3 computes BINARY / SEVERITY / RATIO facts from
these; PATTERN definitions are reference data (disease inference is Layer 4's
graph traversal).

Feature-id convention
---------------------
Binary ids are ``<canonical name>_<low|high>`` where the name comes from
``biomarkers.CODE_TO_NAME`` (``LDL`` → ``ldl_cholesterol`` →
``ldl_cholesterol_high``).

Threshold convention
--------------------
``*_low`` stores its cutoff in ``threshold_high`` (abnormal if value < it);
``*_high`` stores its cutoff in ``threshold_low`` (abnormal if value > it).
Cut-points are the adult desirable bounds (see ``reference_ranges.py``); the
per-patient decision is made against the DB range in Layer 2.

Note on the ``*_low`` features: the seeded lipid ranges are one-sided (only an
upper bound for the atherogenic markers, only a lower bound for HDL), so Layer 2
never flags those sides by default. The definitions are kept because they carry
the clinical cut-point and do fire when a laboratory seeds a two-sided range.
"""

from __future__ import annotations

from typing import Dict, List

from domains.feature_types import VALID_FEATURE_TYPES, FeatureDefinition

# ──────────────────────────────────────────────────────────────────────────────
# 1. BINARY FEATURES — single biomarker outside its desirable range
# ──────────────────────────────────────────────────────────────────────────────
BINARY_FEATURES: Dict[str, FeatureDefinition] = {
    "total_cholesterol_high": FeatureDefinition(
        feature_id="total_cholesterol_high", feature_name="Total Cholesterol High",
        biomarker_id="CHOL", feature_type="BINARY",
        threshold_low=200.0, unit="mg/dL",
        clinical_significance="indicates_hypercholesterolemia",
        description="Total cholesterol above the desirable cut-point (> 200 mg/dL).",
    ),
    "total_cholesterol_low": FeatureDefinition(
        feature_id="total_cholesterol_low", feature_name="Total Cholesterol Low",
        biomarker_id="CHOL", feature_type="BINARY",
        threshold_high=120.0, unit="mg/dL",
        clinical_significance="indicates_hypocholesterolemia",
        description="Total cholesterol unusually low (< 120 mg/dL) — malnutrition, "
                    "hyperthyroidism, or liver disease.",
    ),
    "ldl_cholesterol_high": FeatureDefinition(
        feature_id="ldl_cholesterol_high", feature_name="LDL Cholesterol High",
        biomarker_id="LDL", feature_type="BINARY",
        threshold_low=100.0, unit="mg/dL",
        clinical_significance="indicates_elevated_atherogenic_risk",
        description="LDL above optimal (> 100 mg/dL) — the primary target of "
                    "lipid-lowering therapy.",
    ),
    "ldl_cholesterol_low": FeatureDefinition(
        feature_id="ldl_cholesterol_low", feature_name="LDL Cholesterol Low",
        biomarker_id="LDL", feature_type="BINARY",
        threshold_high=40.0, unit="mg/dL",
        clinical_significance="not_usually_significant",
        description="LDL very low (< 40 mg/dL) — expected on intensive therapy.",
    ),
    "hdl_cholesterol_low": FeatureDefinition(
        feature_id="hdl_cholesterol_low", feature_name="HDL Cholesterol Low",
        biomarker_id="HDL", feature_type="BINARY",
        threshold_high=40.0, unit="mg/dL",
        clinical_significance="indicates_cardiovascular_risk_factor",
        description="HDL below the protective threshold (< 40 mg/dL male, "
                    "< 50 mg/dL female) — an independent CV risk factor.",
    ),
    "hdl_cholesterol_high": FeatureDefinition(
        feature_id="hdl_cholesterol_high", feature_name="HDL Cholesterol High",
        biomarker_id="HDL", feature_type="BINARY",
        threshold_low=100.0, unit="mg/dL",
        clinical_significance="usually_protective",
        description="HDL markedly raised (> 100 mg/dL) — generally protective; "
                    "very high values warrant confirming the assay.",
    ),
    "triglycerides_high": FeatureDefinition(
        feature_id="triglycerides_high", feature_name="Triglycerides High",
        biomarker_id="TRIG", feature_type="BINARY",
        threshold_low=150.0, unit="mg/dL",
        clinical_significance="indicates_hypertriglyceridemia",
        description="Fasting triglycerides above normal (> 150 mg/dL) — metabolic "
                    "syndrome, insulin resistance, alcohol.",
    ),
    "triglycerides_low": FeatureDefinition(
        feature_id="triglycerides_low", feature_name="Triglycerides Low",
        biomarker_id="TRIG", feature_type="BINARY",
        threshold_high=40.0, unit="mg/dL",
        clinical_significance="not_usually_significant",
        description="Triglycerides very low (< 40 mg/dL) — malnutrition or "
                    "malabsorption if persistent.",
    ),
    "vldl_cholesterol_high": FeatureDefinition(
        feature_id="vldl_cholesterol_high", feature_name="VLDL Cholesterol High",
        biomarker_id="VLDL", feature_type="BINARY",
        threshold_low=30.0, unit="mg/dL",
        clinical_significance="indicates_triglyceride_rich_lipoproteins",
        description="VLDL above normal (> 30 mg/dL) — tracks triglycerides.",
    ),
    "non_hdl_cholesterol_high": FeatureDefinition(
        feature_id="non_hdl_cholesterol_high", feature_name="Non-HDL Cholesterol High",
        biomarker_id="NONHDL", feature_type="BINARY",
        threshold_low=130.0, unit="mg/dL",
        clinical_significance="indicates_total_atherogenic_burden",
        description="Non-HDL above target (> 130 mg/dL) — captures every "
                    "atherogenic particle, the secondary treatment target.",
    ),
    "cholesterol_hdl_ratio_high": FeatureDefinition(
        feature_id="cholesterol_hdl_ratio_high", feature_name="Cholesterol/HDL Ratio High",
        biomarker_id="CHOLHDL", feature_type="BINARY",
        threshold_low=5.0, unit="ratio",
        clinical_significance="indicates_elevated_cardiovascular_risk",
        description="Total cholesterol/HDL ratio above 5.0 — raised atherogenic risk.",
    ),
}


# ──────────────────────────────────────────────────────────────────────────────
# 2. SEVERITY FEATURES — single biomarker graded into bands
#    Lipids grade UPWARD (normal is the lowest band) except HDL, where low is bad.
#    Bands are half-open [low, high) and follow the ATP III risk categories.
# ──────────────────────────────────────────────────────────────────────────────
SEVERITY_FEATURES: Dict[str, FeatureDefinition] = {
    "total_cholesterol_severity": FeatureDefinition(
        feature_id="total_cholesterol_severity", feature_name="Total Cholesterol Severity",
        biomarker_id="CHOL", feature_type="SEVERITY", unit="mg/dL",
        severity_levels=["normal", "mild", "moderate", "severe", "critical"],
        threshold_ranges={
            "normal": (0.0, 200.0),        # desirable
            "mild": (200.0, 240.0),        # borderline high
            "moderate": (240.0, 300.0),    # high
            "severe": (300.0, 400.0),
            "critical": (400.0, 1000.0),   # suggests a familial disorder
        },
        clinical_significance="grades_hypercholesterolemia",
        description="Total cholesterol graded by the ATP III risk categories.",
    ),
    "ldl_cholesterol_severity": FeatureDefinition(
        feature_id="ldl_cholesterol_severity", feature_name="LDL Cholesterol Severity",
        biomarker_id="LDL", feature_type="SEVERITY", unit="mg/dL",
        severity_levels=["normal", "mild", "moderate", "severe", "critical"],
        threshold_ranges={
            "normal": (0.0, 100.0),        # optimal
            "mild": (100.0, 130.0),        # near optimal
            "moderate": (130.0, 160.0),    # borderline high
            "severe": (160.0, 190.0),      # high
            "critical": (190.0, 800.0),    # very high — screen for familial HC
        },
        clinical_significance="grades_atherogenic_risk",
        description="LDL graded by the ATP III categories; ≥ 190 mg/dL prompts a "
                    "familial hypercholesterolaemia workup.",
    ),
    "triglycerides_severity": FeatureDefinition(
        feature_id="triglycerides_severity", feature_name="Triglycerides Severity",
        biomarker_id="TRIG", feature_type="SEVERITY", unit="mg/dL",
        severity_levels=["normal", "mild", "moderate", "severe", "critical"],
        threshold_ranges={
            "normal": (0.0, 150.0),
            "mild": (150.0, 200.0),        # borderline high
            "moderate": (200.0, 500.0),    # high
            "severe": (500.0, 1000.0),     # very high
            "critical": (1000.0, 10000.0), # acute pancreatitis risk
        },
        clinical_significance="grades_hypertriglyceridemia",
        description="Triglyceride severity; above 1000 mg/dL carries acute "
                    "pancreatitis risk and needs urgent treatment.",
    ),
    "hdl_cholesterol_severity": FeatureDefinition(
        feature_id="hdl_cholesterol_severity", feature_name="HDL Deficiency Severity",
        biomarker_id="HDL", feature_type="SEVERITY", unit="mg/dL",
        severity_levels=["normal", "mild", "moderate", "severe", "critical"],
        threshold_ranges={
            "critical": (0.0, 20.0),       # consider a genetic HDL disorder
            "severe": (20.0, 30.0),
            "moderate": (30.0, 40.0),
            "mild": (40.0, 50.0),
            "normal": (50.0, 200.0),
        },
        clinical_significance="grades_hdl_deficiency",
        description="HDL deficiency severity — graded downward, since low HDL is "
                    "the abnormal side.",
    ),
}


# ──────────────────────────────────────────────────────────────────────────────
# 3. COMPUTED RATIO FEATURES — derived from multiple biomarkers
#    ``calculation_method`` is "<name> / <name>" using canonical names.
# ──────────────────────────────────────────────────────────────────────────────
COMPUTED_RATIO_FEATURES: Dict[str, FeatureDefinition] = {
    "chol_hdl_ratio": FeatureDefinition(
        feature_id="chol_hdl_ratio", feature_name="Total Cholesterol/HDL Ratio",
        feature_type="RATIO",
        calculation_method="total_cholesterol / hdl_cholesterol",
        clinical_significance="composite_cardiovascular_risk_marker",
        description="Castelli risk index I; < 5 desirable, < 3.5 optimal.",
    ),
    "ldl_hdl_ratio": FeatureDefinition(
        feature_id="ldl_hdl_ratio", feature_name="LDL/HDL Ratio",
        feature_type="RATIO",
        calculation_method="ldl_cholesterol / hdl_cholesterol",
        clinical_significance="atherogenic_balance_marker",
        description="Castelli risk index II; < 3.0 desirable.",
    ),
    "trig_hdl_ratio": FeatureDefinition(
        feature_id="trig_hdl_ratio", feature_name="Triglyceride/HDL Ratio",
        feature_type="RATIO",
        calculation_method="triglycerides / hdl_cholesterol",
        clinical_significance="insulin_resistance_marker",
        description="Triglyceride/HDL ratio; > 3 suggests insulin resistance and "
                    "small dense LDL particles.",
    ),
}


# ──────────────────────────────────────────────────────────────────────────────
# 4. PATTERN FEATURES — compositions of other features into disease patterns
#    Reference data only: disease inference happens in Layer 4 (graph traversal).
# ──────────────────────────────────────────────────────────────────────────────
PATTERN_FEATURES: Dict[str, FeatureDefinition] = {
    "hypercholesterolemia_pattern": FeatureDefinition(
        feature_id="hypercholesterolemia_pattern", feature_name="Hypercholesterolaemia Pattern",
        feature_type="PATTERN",
        feature_composition=["total_cholesterol_high", "ldl_cholesterol_high"],
        clinical_significance="suggests_hypercholesterolemia",
        description="Raised total cholesterol driven by raised LDL.",
    ),
    "atherogenic_dyslipidemia_pattern": FeatureDefinition(
        feature_id="atherogenic_dyslipidemia_pattern",
        feature_name="Atherogenic Dyslipidaemia Pattern",
        feature_type="PATTERN",
        feature_composition=["triglycerides_high", "hdl_cholesterol_low"],
        clinical_significance="suggests_metabolic_syndrome",
        description="The high-triglyceride / low-HDL pair typical of insulin "
                    "resistance and metabolic syndrome.",
    ),
    "mixed_dyslipidemia_pattern": FeatureDefinition(
        feature_id="mixed_dyslipidemia_pattern", feature_name="Mixed Dyslipidaemia Pattern",
        feature_type="PATTERN",
        feature_composition=["ldl_cholesterol_high", "triglycerides_high"],
        clinical_significance="suggests_combined_hyperlipidemia",
        description="Both LDL and triglycerides raised — combined hyperlipidaemia.",
    ),
    "familial_hypercholesterolemia_suspect": FeatureDefinition(
        feature_id="familial_hypercholesterolemia_suspect",
        feature_name="Familial Hypercholesterolaemia (suspected)",
        feature_type="PATTERN",
        feature_composition=["ldl_cholesterol_high"],
        calculation_method="ldl_cholesterol >= 190",
        unit="mg/dL",
        clinical_significance="suggests_familial_hypercholesterolemia",
        description="LDL ≥ 190 mg/dL — screen relatives and consider genetic testing.",
    ),
    "severe_hypertriglyceridemia_pattern": FeatureDefinition(
        feature_id="severe_hypertriglyceridemia_pattern",
        feature_name="Severe Hypertriglyceridaemia",
        feature_type="PATTERN",
        feature_composition=["triglycerides_high"],
        calculation_method="triglycerides >= 500",
        unit="mg/dL",
        clinical_significance="pancreatitis_risk",
        description="Triglycerides ≥ 500 mg/dL — pancreatitis risk; ≥ 1000 is urgent.",
    ),
    "isolated_low_hdl_pattern": FeatureDefinition(
        feature_id="isolated_low_hdl_pattern", feature_name="Isolated Low HDL Pattern",
        feature_type="PATTERN",
        feature_composition=["hdl_cholesterol_low"],
        clinical_significance="independent_cardiovascular_risk",
        description="Low HDL with otherwise acceptable lipids — still a CV risk factor.",
    ),
    "high_atherogenic_burden_pattern": FeatureDefinition(
        feature_id="high_atherogenic_burden_pattern",
        feature_name="High Atherogenic Burden Pattern",
        feature_type="PATTERN",
        feature_composition=["non_hdl_cholesterol_high", "cholesterol_hdl_ratio_high"],
        clinical_significance="suggests_high_cardiovascular_risk",
        description="Non-HDL and the cholesterol/HDL ratio both above target — "
                    "high total atherogenic particle burden.",
    ),
}


# ──────────────────────────────────────────────────────────────────────────────
# Registry + helper functions
# ──────────────────────────────────────────────────────────────────────────────
FEATURE_REGISTRY: Dict[str, FeatureDefinition] = {
    **BINARY_FEATURES,
    **SEVERITY_FEATURES,
    **COMPUTED_RATIO_FEATURES,
    **PATTERN_FEATURES,
}


def get_feature(feature_id: str) -> FeatureDefinition:
    """Return the :class:`FeatureDefinition` for ``feature_id`` (KeyError if unknown)."""
    try:
        return FEATURE_REGISTRY[feature_id]
    except KeyError:
        raise KeyError(f"Unknown feature_id: {feature_id!r}")


def list_all_features() -> List[FeatureDefinition]:
    """Return every defined feature across all categories."""
    return list(FEATURE_REGISTRY.values())


def get_features_by_type(feature_type: str) -> List[FeatureDefinition]:
    """Return all features of the given type (BINARY/SEVERITY/RATIO/PATTERN)."""
    ft = feature_type.strip().upper()
    return [f for f in FEATURE_REGISTRY.values() if f.feature_type == ft]


__all__ = [
    "VALID_FEATURE_TYPES",
    "FeatureDefinition",
    "BINARY_FEATURES",
    "SEVERITY_FEATURES",
    "COMPUTED_RATIO_FEATURES",
    "PATTERN_FEATURES",
    "FEATURE_REGISTRY",
    "get_feature",
    "list_all_features",
    "get_features_by_type",
]
