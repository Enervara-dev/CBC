"""
LFT Layer-3 feature definitions (liver panel knowledge base).

A declarative catalogue: WHAT clinical features exist for a liver panel and the
medical logic behind each. Layer 3 computes BINARY / SEVERITY / RATIO facts from
these; PATTERN definitions are reference data (disease inference is Layer 4's
graph traversal).

Feature-id convention
---------------------
Binary ids are ``<canonical name>_<low|high>`` where the name comes from
``biomarkers.CODE_TO_NAME`` (``ALT`` → ``alt`` → ``alt_high``), because the
Layer-3 generator builds ids that way from each biomarker's LOW/HIGH status.

Threshold convention
--------------------
``*_low`` stores its cutoff in ``threshold_high`` (abnormal if value < it);
``*_high`` stores its cutoff in ``threshold_low`` (abnormal if value > it).
Cutoffs below are the adult generic bounds (see ``reference_ranges.py``); the
per-patient decision is made against the DB range in Layer 2.
"""

from __future__ import annotations

from typing import Dict, List

from domains.feature_types import VALID_FEATURE_TYPES, FeatureDefinition

# ──────────────────────────────────────────────────────────────────────────────
# 1. BINARY FEATURES — single biomarker in/out of reference range
# ──────────────────────────────────────────────────────────────────────────────
BINARY_FEATURES: Dict[str, FeatureDefinition] = {
    "alt_high": FeatureDefinition(
        feature_id="alt_high", feature_name="ALT (SGPT) High",
        biomarker_id="ALT", feature_type="BINARY",
        threshold_low=40.0, unit="U/L",
        clinical_significance="indicates_hepatocellular_injury",
        description="ALT above reference range (> 40 U/L) — hepatocyte damage.",
    ),
    "alt_low": FeatureDefinition(
        feature_id="alt_low", feature_name="ALT (SGPT) Low",
        biomarker_id="ALT", feature_type="BINARY",
        threshold_high=7.0, unit="U/L",
        clinical_significance="rarely_significant",
        description="ALT below reference range (< 7 U/L) — rarely clinically significant.",
    ),
    "ast_high": FeatureDefinition(
        feature_id="ast_high", feature_name="AST (SGOT) High",
        biomarker_id="AST", feature_type="BINARY",
        threshold_low=40.0, unit="U/L",
        clinical_significance="indicates_hepatocellular_or_muscle_injury",
        description="AST above reference range (> 40 U/L); not liver-specific "
                    "(cardiac/skeletal muscle also raise it).",
    ),
    "ast_low": FeatureDefinition(
        feature_id="ast_low", feature_name="AST (SGOT) Low",
        biomarker_id="AST", feature_type="BINARY",
        threshold_high=9.0, unit="U/L",
        clinical_significance="rarely_significant",
        description="AST below reference range (< 9 U/L) — rarely clinically significant.",
    ),
    "alp_high": FeatureDefinition(
        feature_id="alp_high", feature_name="Alkaline Phosphatase High",
        biomarker_id="ALP", feature_type="BINARY",
        threshold_low=129.0, unit="U/L",
        clinical_significance="indicates_cholestasis_or_bone_turnover",
        description="ALP above reference range (> 129 U/L) — biliary obstruction, "
                    "infiltrative liver disease, or bone origin.",
    ),
    "alp_low": FeatureDefinition(
        feature_id="alp_low", feature_name="Alkaline Phosphatase Low",
        biomarker_id="ALP", feature_type="BINARY",
        threshold_high=40.0, unit="U/L",
        clinical_significance="indicates_hypophosphatasia_or_malnutrition",
        description="ALP below reference range (< 40 U/L) — zinc/magnesium deficiency, "
                    "hypothyroidism, hypophosphatasia.",
    ),
    "ggt_high": FeatureDefinition(
        feature_id="ggt_high", feature_name="GGT High",
        biomarker_id="GGT", feature_type="BINARY",
        threshold_low=61.0, unit="U/L",
        clinical_significance="confirms_hepatobiliary_origin_of_alp",
        description="GGT above reference range (> 61 U/L) — cholestasis, alcohol, "
                    "enzyme-inducing drugs; confirms a raised ALP is hepatic.",
    ),
    "ggt_low": FeatureDefinition(
        feature_id="ggt_low", feature_name="GGT Low",
        biomarker_id="GGT", feature_type="BINARY",
        threshold_high=8.0, unit="U/L",
        clinical_significance="rarely_significant",
        description="GGT below reference range (< 8 U/L) — rarely clinically significant.",
    ),
    "total_bilirubin_high": FeatureDefinition(
        feature_id="total_bilirubin_high", feature_name="Total Bilirubin High",
        biomarker_id="TBIL", feature_type="BINARY",
        threshold_low=1.2, unit="mg/dL",
        clinical_significance="indicates_hyperbilirubinemia",
        description="Total bilirubin above reference range (> 1.2 mg/dL); "
                    "clinical jaundice appears above ~2.5 mg/dL.",
    ),
    "total_bilirubin_low": FeatureDefinition(
        feature_id="total_bilirubin_low", feature_name="Total Bilirubin Low",
        biomarker_id="TBIL", feature_type="BINARY",
        threshold_high=0.2, unit="mg/dL",
        clinical_significance="not_clinically_significant",
        description="Total bilirubin below reference range (< 0.2 mg/dL).",
    ),
    "direct_bilirubin_high": FeatureDefinition(
        feature_id="direct_bilirubin_high", feature_name="Direct Bilirubin High",
        biomarker_id="DBIL", feature_type="BINARY",
        threshold_low=0.3, unit="mg/dL",
        clinical_significance="indicates_conjugated_hyperbilirubinemia",
        description="Direct (conjugated) bilirubin above range (> 0.3 mg/dL) — "
                    "hepatocellular disease or biliary obstruction.",
    ),
    "indirect_bilirubin_high": FeatureDefinition(
        feature_id="indirect_bilirubin_high", feature_name="Indirect Bilirubin High",
        biomarker_id="IBIL", feature_type="BINARY",
        threshold_low=0.9, unit="mg/dL",
        clinical_significance="indicates_unconjugated_hyperbilirubinemia",
        description="Indirect (unconjugated) bilirubin above range (> 0.9 mg/dL) — "
                    "haemolysis or Gilbert syndrome.",
    ),
    "albumin_low": FeatureDefinition(
        feature_id="albumin_low", feature_name="Albumin Low",
        biomarker_id="ALB", feature_type="BINARY",
        threshold_high=3.5, unit="g/dL",
        clinical_significance="indicates_impaired_hepatic_synthesis",
        description="Albumin below reference range (< 3.5 g/dL) — chronic liver "
                    "disease, malnutrition, protein loss, or inflammation.",
    ),
    "albumin_high": FeatureDefinition(
        feature_id="albumin_high", feature_name="Albumin High",
        biomarker_id="ALB", feature_type="BINARY",
        threshold_low=5.2, unit="g/dL",
        clinical_significance="indicates_dehydration",
        description="Albumin above reference range (> 5.2 g/dL) — usually haemoconcentration.",
    ),
    "total_protein_low": FeatureDefinition(
        feature_id="total_protein_low", feature_name="Total Protein Low",
        biomarker_id="TP", feature_type="BINARY",
        threshold_high=6.0, unit="g/dL",
        clinical_significance="indicates_hypoproteinemia",
        description="Total protein below reference range (< 6.0 g/dL).",
    ),
    "total_protein_high": FeatureDefinition(
        feature_id="total_protein_high", feature_name="Total Protein High",
        biomarker_id="TP", feature_type="BINARY",
        threshold_low=8.3, unit="g/dL",
        clinical_significance="indicates_hyperproteinemia_or_paraproteinemia",
        description="Total protein above reference range (> 8.3 g/dL) — chronic "
                    "inflammation, myeloma, dehydration.",
    ),
    "globulin_high": FeatureDefinition(
        feature_id="globulin_high", feature_name="Globulin High",
        biomarker_id="GLOB", feature_type="BINARY",
        threshold_low=3.5, unit="g/dL",
        clinical_significance="indicates_chronic_inflammation_or_cirrhosis",
        description="Globulin above reference range (> 3.5 g/dL) — chronic liver "
                    "disease, chronic infection, gammopathy.",
    ),
    "globulin_low": FeatureDefinition(
        feature_id="globulin_low", feature_name="Globulin Low",
        biomarker_id="GLOB", feature_type="BINARY",
        threshold_high=2.0, unit="g/dL",
        clinical_significance="indicates_immunodeficiency_or_protein_loss",
        description="Globulin below reference range (< 2.0 g/dL).",
    ),
    "ag_ratio_low": FeatureDefinition(
        feature_id="ag_ratio_low", feature_name="A/G Ratio Low",
        biomarker_id="AGR", feature_type="BINARY",
        threshold_high=1.0, unit="ratio",
        clinical_significance="indicates_chronic_liver_disease",
        description="Albumin/globulin ratio below 1.0 — reversal typical of "
                    "cirrhosis or chronic inflammation.",
    ),
    "ag_ratio_high": FeatureDefinition(
        feature_id="ag_ratio_high", feature_name="A/G Ratio High",
        biomarker_id="AGR", feature_type="BINARY",
        threshold_low=2.5, unit="ratio",
        clinical_significance="indicates_low_globulin_states",
        description="Albumin/globulin ratio above 2.5 — low globulin production.",
    ),
}


# ──────────────────────────────────────────────────────────────────────────────
# 2. SEVERITY FEATURES — single biomarker graded into bands
#    Enzymes/bilirubin grade UPWARD (normal is the lowest band); albumin grades
#    DOWNWARD like a cytopenia. Bands are half-open [low, high).
# ──────────────────────────────────────────────────────────────────────────────
SEVERITY_FEATURES: Dict[str, FeatureDefinition] = {
    "alt_severity": FeatureDefinition(
        feature_id="alt_severity", feature_name="ALT Elevation Severity",
        biomarker_id="ALT", feature_type="SEVERITY", unit="U/L",
        severity_levels=["normal", "mild", "moderate", "severe", "critical"],
        threshold_ranges={
            "normal": (0.0, 40.0),
            "mild": (40.0, 120.0),        # < 3× ULN
            "moderate": (120.0, 200.0),   # 3–5× ULN
            "severe": (200.0, 400.0),     # 5–10× ULN
            "critical": (400.0, 20000.0), # > 10× ULN — acute hepatitis / toxic injury
        },
        clinical_significance="grades_hepatocellular_injury",
        description="ALT elevation graded as multiples of the upper limit of normal.",
    ),
    "ast_severity": FeatureDefinition(
        feature_id="ast_severity", feature_name="AST Elevation Severity",
        biomarker_id="AST", feature_type="SEVERITY", unit="U/L",
        severity_levels=["normal", "mild", "moderate", "severe", "critical"],
        threshold_ranges={
            "normal": (0.0, 40.0),
            "mild": (40.0, 120.0),
            "moderate": (120.0, 200.0),
            "severe": (200.0, 400.0),
            "critical": (400.0, 20000.0),
        },
        clinical_significance="grades_hepatocellular_injury",
        description="AST elevation graded as multiples of the upper limit of normal.",
    ),
    "total_bilirubin_severity": FeatureDefinition(
        feature_id="total_bilirubin_severity", feature_name="Bilirubin Severity",
        biomarker_id="TBIL", feature_type="SEVERITY", unit="mg/dL",
        severity_levels=["normal", "mild", "moderate", "severe", "critical"],
        threshold_ranges={
            "normal": (0.0, 1.2),
            "mild": (1.2, 3.0),        # sub-clinical / early jaundice
            "moderate": (3.0, 5.0),    # clinically visible jaundice
            "severe": (5.0, 15.0),
            "critical": (15.0, 60.0),  # deep jaundice — liver failure workup
        },
        clinical_significance="grades_hyperbilirubinemia",
        description="Hyperbilirubinaemia severity; jaundice is visible above ~2.5–3 mg/dL.",
    ),
    "albumin_severity": FeatureDefinition(
        feature_id="albumin_severity", feature_name="Hypoalbuminaemia Severity",
        biomarker_id="ALB", feature_type="SEVERITY", unit="g/dL",
        severity_levels=["normal", "mild", "moderate", "severe", "critical"],
        threshold_ranges={
            "critical": (0.0, 2.0),
            "severe": (2.0, 2.5),
            "moderate": (2.5, 3.0),
            "mild": (3.0, 3.5),
            "normal": (3.5, 7.0),
        },
        clinical_significance="grades_synthetic_dysfunction",
        description="Hypoalbuminaemia severity — a proxy for hepatic synthetic function "
                    "(also falls in inflammation and protein loss).",
    ),
}


# ──────────────────────────────────────────────────────────────────────────────
# 3. COMPUTED RATIO FEATURES — derived from multiple biomarkers
#    ``calculation_method`` is "<name> / <name>" using canonical names.
# ──────────────────────────────────────────────────────────────────────────────
COMPUTED_RATIO_FEATURES: Dict[str, FeatureDefinition] = {
    "de_ritis_ratio": FeatureDefinition(
        feature_id="de_ritis_ratio", feature_name="De Ritis Ratio (AST/ALT)",
        feature_type="RATIO",
        calculation_method="ast / alt",
        threshold_low=2.0,   # emits de_ritis_ratio_high
        clinical_significance="differentiates_alcoholic_vs_viral_liver_injury",
        description="AST/ALT ratio; > 2 suggests alcoholic liver disease, "
                    "< 1 suggests viral hepatitis or NAFLD, > 1 in cirrhosis.",
    ),
    "albumin_globulin_ratio": FeatureDefinition(
        feature_id="albumin_globulin_ratio", feature_name="Albumin/Globulin Ratio",
        feature_type="RATIO",
        calculation_method="albumin / globulin",
        clinical_significance="reflects_synthetic_function_and_gammopathy",
        description="Computed A/G ratio; reversal (< 1) points to chronic liver "
                    "disease or a gammopathy.",
    ),
    "conjugated_bilirubin_fraction": FeatureDefinition(
        feature_id="conjugated_bilirubin_fraction",
        feature_name="Conjugated Bilirubin Fraction",
        feature_type="RATIO",
        calculation_method="direct_bilirubin / total_bilirubin",
        threshold_low=0.5,    # conjugated_bilirubin_fraction_high: predominantly conjugated
        threshold_high=0.2,   # conjugated_bilirubin_fraction_low: predominantly unconjugated
        clinical_significance="separates_conjugated_vs_unconjugated_jaundice",
        description="Direct/total bilirubin; > 0.5 conjugated (hepatic/obstructive), "
                    "< 0.2 unconjugated (haemolysis, Gilbert).",
    ),
    "alt_alp_ratio": FeatureDefinition(
        feature_id="alt_alp_ratio", feature_name="ALT/ALP Ratio (R factor proxy)",
        feature_type="RATIO",
        calculation_method="alt / alp",
        clinical_significance="classifies_injury_pattern",
        description="Proxy for the R factor: high = hepatocellular injury, "
                    "low = cholestatic injury, intermediate = mixed.",
    ),
    # Enzymes scaled to *this patient's* upper limit (``<name>_xuln``), so sex,
    # age and pregnancy ranges carry into the pattern (ACG 2017).
    "r_factor": FeatureDefinition(
        feature_id="r_factor", feature_name="R factor ((ALT/ULN) / (ALP/ULN))",
        feature_type="RATIO",
        calculation_method="alt_xuln / alp_xuln",
        threshold_low=5.0,    # r_factor_high: hepatocellular
        threshold_high=2.0,   # r_factor_low: cholestatic
        clinical_significance="classifies_injury_pattern",
        description="R >= 5 hepatocellular, R <= 2 cholestatic, between them mixed.",
    ),
    "alp_xuln": FeatureDefinition(
        feature_id="alp_xuln", feature_name="ALP as a multiple of its upper limit",
        feature_type="RATIO",
        calculation_method="alp_xuln",
        threshold_low=1.5,    # alp_xuln_high
        clinical_significance="separates_significant_from_nonspecific_alp_rise",
        description="ALP / upper limit; rises below 1.5 x are often non-specific.",
    ),
    "total_bilirubin_xuln": FeatureDefinition(
        feature_id="total_bilirubin_xuln", feature_name="Bilirubin as a multiple of its upper limit",
        feature_type="RATIO",
        calculation_method="total_bilirubin_xuln",
        threshold_low=2.0,    # total_bilirubin_xuln_high (Hy's law bilirubin criterion)
        clinical_significance="grades_jaundice_in_liver_injury",
        description="Total bilirubin / upper limit; above 2 x with ALT > 10 x ULN marks "
                    "severe acute injury.",
    ),
}


# ──────────────────────────────────────────────────────────────────────────────
# 4. PATTERN FEATURES — compositions of other features into disease patterns
#    Reference data only: disease inference happens in Layer 4 (graph traversal).
# ──────────────────────────────────────────────────────────────────────────────
PATTERN_FEATURES: Dict[str, FeatureDefinition] = {
    "hepatocellular_pattern": FeatureDefinition(
        feature_id="hepatocellular_pattern", feature_name="Hepatocellular Injury Pattern",
        feature_type="PATTERN",
        feature_composition=["alt_high", "ast_high"],
        clinical_significance="suggests_hepatocellular_injury",
        description="Transaminases raised out of proportion to ALP — hepatitis, "
                    "NAFLD, drug injury, ischaemia.",
    ),
    "cholestatic_pattern": FeatureDefinition(
        feature_id="cholestatic_pattern", feature_name="Cholestatic Pattern",
        feature_type="PATTERN",
        feature_composition=["alp_high", "ggt_high"],
        clinical_significance="suggests_cholestasis",
        description="ALP raised with a confirming GGT — biliary obstruction, PBC, "
                    "infiltrative disease.",
    ),
    "mixed_liver_injury_pattern": FeatureDefinition(
        feature_id="mixed_liver_injury_pattern", feature_name="Mixed Liver Injury Pattern",
        feature_type="PATTERN",
        feature_composition=["alt_high", "alp_high"],
        clinical_significance="suggests_mixed_injury",
        description="Both hepatocellular and cholestatic markers raised — often drug-induced.",
    ),
    "synthetic_dysfunction_pattern": FeatureDefinition(
        feature_id="synthetic_dysfunction_pattern",
        feature_name="Hepatic Synthetic Dysfunction Pattern",
        feature_type="PATTERN",
        feature_composition=["albumin_low", "ag_ratio_low"],
        clinical_significance="suggests_chronic_liver_disease",
        description="Low albumin with a reversed A/G ratio — impaired synthesis, "
                    "typical of cirrhosis.",
    ),
    "jaundice_pattern": FeatureDefinition(
        feature_id="jaundice_pattern", feature_name="Jaundice Pattern",
        feature_type="PATTERN",
        feature_composition=["total_bilirubin_high", "direct_bilirubin_high"],
        clinical_significance="suggests_conjugated_hyperbilirubinemia",
        description="Raised total with raised direct bilirubin — hepatic or "
                    "post-hepatic (obstructive) jaundice.",
    ),
    "alcoholic_liver_pattern": FeatureDefinition(
        feature_id="alcoholic_liver_pattern", feature_name="Alcoholic Liver Disease Pattern",
        feature_type="PATTERN",
        feature_composition=["ast_high", "ggt_high"],
        calculation_method="ast / alt > 2",
        clinical_significance="suggests_alcoholic_liver_disease",
        description="AST-predominant rise (AST/ALT > 2) with high GGT.",
    ),
    "gilbert_pattern": FeatureDefinition(
        feature_id="gilbert_pattern", feature_name="Gilbert Syndrome Pattern",
        feature_type="PATTERN",
        feature_composition=["indirect_bilirubin_high"],
        calculation_method="indirect bilirubin high with normal ALT/AST/ALP",
        clinical_significance="suggests_gilbert_syndrome",
        description="Isolated unconjugated hyperbilirubinaemia with otherwise "
                    "normal liver enzymes (haemolysis must be excluded).",
    ),
    "biliary_obstruction_pattern": FeatureDefinition(
        feature_id="biliary_obstruction_pattern", feature_name="Biliary Obstruction Pattern",
        feature_type="PATTERN",
        feature_composition=["alp_high", "ggt_high", "direct_bilirubin_high"],
        clinical_significance="suggests_biliary_obstruction",
        description="Cholestatic enzymes with conjugated hyperbilirubinaemia — "
                    "imaging of the biliary tree is indicated.",
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
