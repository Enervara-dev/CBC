"""
Clinical feature definitions and disease-pattern library (Layer 3 knowledge base).

This module is a *declarative catalogue* — it defines WHAT clinical features exist
and the medical logic behind each, but does not compute them (that's the
evaluator's job in a later module). Keeping definitions data-driven and auditable
is the "knowledge-graph-first" principle applied to feature generation.

Feature types
-------------
BINARY   : a single biomarker in/out of range (e.g. ``hemoglobin_low``).
SEVERITY : a single biomarker graded into bands (e.g. ``hemoglobin_severity``).
RATIO    : a value computed from several biomarkers (e.g. ``rbc_index``).
PATTERN  : a composition of other features into a disease pattern
           (e.g. ``microcytic_pattern``, ``iron_deficiency_anemia``).

BINARY threshold convention (matches the task spec)
---------------------------------------------------
A ``*_low`` feature stores its cutoff in ``threshold_high`` and is TRUE when
``value < threshold_high``. A ``*_high`` feature stores its cutoff in
``threshold_low`` and is TRUE when ``value > threshold_low``. (i.e. the stored
threshold is always the reference bound that the value must cross to be abnormal.)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

VALID_FEATURE_TYPES = ("BINARY", "SEVERITY", "RATIO", "PATTERN")


@dataclass
class FeatureDefinition:
    """
    One clinical feature in the library.

    Attributes
    ----------
    feature_id : str               unique key, e.g. "hemoglobin_low"
    feature_type : str             one of VALID_FEATURE_TYPES
    feature_name : str             human-readable name
    biomarker_id : str             primary biomarker (BINARY/SEVERITY); "" for multi-marker
    threshold_low : Optional[float]   cutoff for "*_high" features (abnormal if value > this)
    threshold_high : Optional[float]  cutoff for "*_low" features (abnormal if value < this)
    unit : Optional[str]           unit the thresholds are expressed in
    severity_levels : Optional[List[str]]   ordered bands (SEVERITY)
    threshold_ranges : Optional[Dict[str, Tuple[float, float]]]
                                   band -> (min, max) interval (SEVERITY)
    feature_composition : Optional[List[str]]   referenced feature_ids (PATTERN)
    calculation_method : Optional[str]   formula / rule (RATIO, PATTERN)
    clinical_significance : str    what abnormality this indicates (audit trail)
    description : str              plain-language description (audit trail)
    """

    feature_id: str
    feature_type: str
    feature_name: str = ""
    biomarker_id: str = ""
    threshold_low: Optional[float] = None
    threshold_high: Optional[float] = None
    unit: Optional[str] = None
    severity_levels: Optional[List[str]] = None
    threshold_ranges: Optional[Dict[str, Tuple[float, float]]] = None
    feature_composition: Optional[List[str]] = None
    calculation_method: Optional[str] = None
    clinical_significance: str = ""
    description: str = ""

    def as_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict (audit / logging / JSON)."""
        return asdict(self)


# ──────────────────────────────────────────────────────────────────────────────
# 1. BINARY FEATURES — single biomarker in/out of reference range
#    (cutoffs are adult generic CBC reference bounds)
# ──────────────────────────────────────────────────────────────────────────────
BINARY_FEATURES: Dict[str, FeatureDefinition] = {
    "hemoglobin_low": FeatureDefinition(
        feature_id="hemoglobin_low", feature_name="Hemoglobin Low",
        biomarker_id="HGB", feature_type="BINARY",
        threshold_high=12.0, unit="g/dL",
        clinical_significance="indicates_anemia",
        description="Hemoglobin below reference range (< 12.0 g/dL).",
    ),
    "hemoglobin_high": FeatureDefinition(
        feature_id="hemoglobin_high", feature_name="Hemoglobin High",
        biomarker_id="HGB", feature_type="BINARY",
        threshold_low=16.0, unit="g/dL",
        clinical_significance="indicates_polycythemia",
        description="Hemoglobin above reference range (> 16.0 g/dL).",
    ),
    "hematocrit_low": FeatureDefinition(
        feature_id="hematocrit_low", feature_name="Hematocrit Low",
        biomarker_id="HCT", feature_type="BINARY",
        threshold_high=36.0, unit="%",
        clinical_significance="indicates_anemia",
        description="Hematocrit below reference range (< 36 %).",
    ),
    "hematocrit_high": FeatureDefinition(
        feature_id="hematocrit_high", feature_name="Hematocrit High",
        biomarker_id="HCT", feature_type="BINARY",
        threshold_low=50.0, unit="%",
        clinical_significance="indicates_polycythemia_or_dehydration",
        description="Hematocrit above reference range (> 50 %).",
    ),
    "rbc_low": FeatureDefinition(
        feature_id="rbc_low", feature_name="RBC Count Low",
        biomarker_id="RBC", feature_type="BINARY",
        threshold_high=4.2, unit="M/uL",
        clinical_significance="indicates_anemia",
        description="Red blood cell count below reference range (< 4.2 M/uL).",
    ),
    "rbc_high": FeatureDefinition(
        feature_id="rbc_high", feature_name="RBC Count High",
        biomarker_id="RBC", feature_type="BINARY",
        threshold_low=5.9, unit="M/uL",
        clinical_significance="indicates_erythrocytosis",
        description="Red blood cell count above reference range (> 5.9 M/uL).",
    ),
    "wbc_low": FeatureDefinition(
        feature_id="wbc_low", feature_name="WBC Count Low",
        biomarker_id="WBC", feature_type="BINARY",
        threshold_high=4.0, unit="K/uL",
        clinical_significance="indicates_leukopenia",
        description="White blood cell count below reference range (< 4.0 K/uL).",
    ),
    "wbc_high": FeatureDefinition(
        feature_id="wbc_high", feature_name="WBC Count High",
        biomarker_id="WBC", feature_type="BINARY",
        threshold_low=11.0, unit="K/uL",
        clinical_significance="indicates_leukocytosis_or_infection",
        description="White blood cell count above reference range (> 11.0 K/uL).",
    ),
    "platelets_low": FeatureDefinition(
        feature_id="platelets_low", feature_name="Platelet Count Low",
        biomarker_id="PLT", feature_type="BINARY",
        threshold_high=150.0, unit="K/uL",
        clinical_significance="indicates_thrombocytopenia",
        description="Platelet count below reference range (< 150 K/uL).",
    ),
    "platelets_high": FeatureDefinition(
        feature_id="platelets_high", feature_name="Platelet Count High",
        biomarker_id="PLT", feature_type="BINARY",
        threshold_low=400.0, unit="K/uL",
        clinical_significance="indicates_thrombocytosis",
        description="Platelet count above reference range (> 400 K/uL).",
    ),
    "mcv_low": FeatureDefinition(
        feature_id="mcv_low", feature_name="MCV Low",
        biomarker_id="MCV", feature_type="BINARY",
        threshold_high=80.0, unit="fL",
        clinical_significance="indicates_microcytosis",
        description="Mean corpuscular volume below reference range (< 80 fL).",
    ),
    "mcv_high": FeatureDefinition(
        feature_id="mcv_high", feature_name="MCV High",
        biomarker_id="MCV", feature_type="BINARY",
        threshold_low=100.0, unit="fL",
        clinical_significance="indicates_macrocytosis",
        description="Mean corpuscular volume above reference range (> 100 fL).",
    ),
    "mch_low": FeatureDefinition(
        feature_id="mch_low", feature_name="MCH Low",
        biomarker_id="MCH", feature_type="BINARY",
        threshold_high=27.0, unit="pg",
        clinical_significance="indicates_hypochromia",
        description="Mean corpuscular hemoglobin below reference range (< 27 pg).",
    ),
    "mch_high": FeatureDefinition(
        feature_id="mch_high", feature_name="MCH High",
        biomarker_id="MCH", feature_type="BINARY",
        threshold_low=33.0, unit="pg",
        clinical_significance="indicates_hyperchromia",
        description="Mean corpuscular hemoglobin above reference range (> 33 pg).",
    ),
    "mchc_low": FeatureDefinition(
        feature_id="mchc_low", feature_name="MCHC Low",
        biomarker_id="MCHC", feature_type="BINARY",
        threshold_high=32.0, unit="g/dL",
        clinical_significance="indicates_hypochromia",
        description="Mean corpuscular hemoglobin concentration below range (< 32 g/dL).",
    ),
    "mchc_high": FeatureDefinition(
        feature_id="mchc_high", feature_name="MCHC High",
        biomarker_id="MCHC", feature_type="BINARY",
        threshold_low=36.0, unit="g/dL",
        clinical_significance="indicates_spherocytosis",
        description="Mean corpuscular hemoglobin concentration above range (> 36 g/dL).",
    ),
    "rdw_high": FeatureDefinition(
        feature_id="rdw_high", feature_name="RDW High",
        biomarker_id="RDW", feature_type="BINARY",
        threshold_low=14.5, unit="%",
        clinical_significance="indicates_anisocytosis",
        description="Red cell distribution width above range (> 14.5 %) — mixed cell sizes.",
    ),
    "neutrophils_high": FeatureDefinition(
        feature_id="neutrophils_high", feature_name="Neutrophils High",
        biomarker_id="NEUT", feature_type="BINARY",
        threshold_low=70.0, unit="%",
        clinical_significance="indicates_bacterial_infection",
        description="Neutrophil percentage above range (> 70 %).",
    ),
    "neutrophils_low": FeatureDefinition(
        feature_id="neutrophils_low", feature_name="Neutrophils Low",
        biomarker_id="NEUT", feature_type="BINARY",
        threshold_high=40.0, unit="%",
        clinical_significance="indicates_neutropenia",
        description="Neutrophil percentage below range (< 40 %).",
    ),
    "lymphocytes_low": FeatureDefinition(
        feature_id="lymphocytes_low", feature_name="Lymphocytes Low",
        biomarker_id="LYMPH", feature_type="BINARY",
        threshold_high=20.0, unit="%",
        clinical_significance="indicates_lymphopenia",
        description="Lymphocyte percentage below range (< 20 %).",
    ),
    "lymphocytes_high": FeatureDefinition(
        feature_id="lymphocytes_high", feature_name="Lymphocytes High",
        biomarker_id="LYMPH", feature_type="BINARY",
        threshold_low=40.0, unit="%",
        clinical_significance="indicates_lymphocytosis_or_viral_infection",
        description="Lymphocyte percentage above range (> 40 %).",
    ),
}


# ──────────────────────────────────────────────────────────────────────────────
# 2. SEVERITY FEATURES — single biomarker graded into bands
#    threshold_ranges grade the depression below normal (cytopenia severity).
# ──────────────────────────────────────────────────────────────────────────────
SEVERITY_FEATURES: Dict[str, FeatureDefinition] = {
    "hemoglobin_severity": FeatureDefinition(
        feature_id="hemoglobin_severity", feature_name="Hemoglobin Severity",
        biomarker_id="HGB", feature_type="SEVERITY", unit="g/dL",
        severity_levels=["normal", "mild", "moderate", "severe", "critical"],
        threshold_ranges={
            "critical": (0.0, 3.0),
            "severe": (3.0, 7.0),
            "moderate": (7.0, 10.0),
            "mild": (10.0, 12.0),
            "normal": (12.0, 20.0),
        },
        clinical_significance="grades_anemia_severity",
        description="Anemia severity by hemoglobin band (WHO-style grading).",
    ),
    "wbc_severity": FeatureDefinition(
        feature_id="wbc_severity", feature_name="WBC (Leukopenia) Severity",
        biomarker_id="WBC", feature_type="SEVERITY", unit="K/uL",
        severity_levels=["normal", "mild", "moderate", "severe", "critical"],
        threshold_ranges={
            "critical": (0.0, 1.0),
            "severe": (1.0, 2.0),
            "moderate": (2.0, 3.0),
            "mild": (3.0, 4.0),
            "normal": (4.0, 11.0),
        },
        clinical_significance="grades_leukopenia_severity",
        description="Leukopenia severity by absolute WBC band (infection risk grading).",
    ),
    "platelets_severity": FeatureDefinition(
        feature_id="platelets_severity", feature_name="Platelet (Thrombocytopenia) Severity",
        biomarker_id="PLT", feature_type="SEVERITY", unit="K/uL",
        severity_levels=["normal", "mild", "moderate", "severe", "critical"],
        threshold_ranges={
            "critical": (0.0, 10.0),
            "severe": (10.0, 20.0),
            "moderate": (20.0, 50.0),
            "mild": (50.0, 150.0),
            "normal": (150.0, 450.0),
        },
        clinical_significance="grades_thrombocytopenia_severity",
        description="Thrombocytopenia severity by platelet band (bleeding-risk grading).",
    ),
}


# ──────────────────────────────────────────────────────────────────────────────
# 3. COMPUTED RATIO FEATURES — derived from multiple biomarkers
# ──────────────────────────────────────────────────────────────────────────────
COMPUTED_RATIO_FEATURES: Dict[str, FeatureDefinition] = {
    "rbc_index": FeatureDefinition(
        feature_id="rbc_index", feature_name="Hemoglobin-per-RBC Index",
        feature_type="RATIO",
        calculation_method="hemoglobin / rbc",
        clinical_significance="reflects_average_cell_hemoglobin",
        description="Mean hemoglobin per RBC (g/dL per M/uL); low suggests hypochromia.",
    ),
    "wbc_neutrophil_ratio": FeatureDefinition(
        feature_id="wbc_neutrophil_ratio", feature_name="Neutrophil Fraction",
        feature_type="RATIO",
        calculation_method="neutrophils / wbc",
        clinical_significance="reflects_neutrophil_predominance",
        description="Fraction of WBCs that are neutrophils; high in bacterial infection.",
    ),
    "neutrophil_lymphocyte_ratio": FeatureDefinition(
        feature_id="neutrophil_lymphocyte_ratio", feature_name="Neutrophil-to-Lymphocyte Ratio (NLR)",
        feature_type="RATIO",
        calculation_method="neutrophils / lymphocytes",
        clinical_significance="systemic_inflammation_marker",
        description="NLR; elevated values track systemic inflammation / physiological stress.",
    ),
    "rdw_mcv_ratio": FeatureDefinition(
        feature_id="rdw_mcv_ratio", feature_name="RDW-to-MCV Ratio",
        feature_type="RATIO",
        calculation_method="rdw / mcv",
        clinical_significance="anisocytosis_relative_to_cell_size",
        description="RDW relative to cell size; aids early iron-deficiency detection.",
    ),
    "mentzer_index": FeatureDefinition(
        feature_id="mentzer_index", feature_name="Mentzer Index",
        feature_type="RATIO",
        calculation_method="mcv / rbc",
        clinical_significance="differentiates_iron_deficiency_vs_thalassemia",
        description="Mentzer index; > 13 suggests iron deficiency, < 13 suggests thalassemia trait.",
    ),
}


# ──────────────────────────────────────────────────────────────────────────────
# 4. PATTERN FEATURES — compositions of other features into disease patterns
#    feature_composition lists referenced feature_ids (resolved by the evaluator).
# ──────────────────────────────────────────────────────────────────────────────
PATTERN_FEATURES: Dict[str, FeatureDefinition] = {
    "microcytic_pattern": FeatureDefinition(
        feature_id="microcytic_pattern", feature_name="Microcytic Anemia Pattern",
        feature_type="PATTERN",
        feature_composition=["hemoglobin_low", "mcv_low"],
        clinical_significance="suggests_microcytic_anemia",
        description="Low hemoglobin + low MCV (small RBCs); RDW high reinforces it.",
    ),
    "macrocytic_pattern": FeatureDefinition(
        feature_id="macrocytic_pattern", feature_name="Macrocytic Anemia Pattern",
        feature_type="PATTERN",
        feature_composition=["hemoglobin_low", "mcv_high"],
        clinical_significance="suggests_macrocytic_anemia",
        description="Low hemoglobin + high MCV (large RBCs); seen in B12/folate deficiency.",
    ),
    "normocytic_pattern": FeatureDefinition(
        feature_id="normocytic_pattern", feature_name="Normocytic Anemia Pattern",
        feature_type="PATTERN",
        feature_composition=["hemoglobin_low", "mcv_normal"],
        clinical_significance="suggests_normocytic_anemia",
        description="Low hemoglobin with normal MCV; seen in chronic disease / acute loss.",
    ),
    "infection_pattern": FeatureDefinition(
        feature_id="infection_pattern", feature_name="Bacterial Infection Pattern",
        feature_type="PATTERN",
        feature_composition=["wbc_high", "neutrophils_high"],
        clinical_significance="suggests_bacterial_infection",
        description="Leukocytosis with neutrophil predominance.",
    ),
    "immune_compromise_pattern": FeatureDefinition(
        feature_id="immune_compromise_pattern", feature_name="Immune Compromise Pattern",
        feature_type="PATTERN",
        feature_composition=["wbc_low", "lymphocytes_low"],
        clinical_significance="suggests_immunosuppression",
        description="Leukopenia with lymphopenia; reduced immune reserve.",
    ),
    "thrombocytopenia_severe": FeatureDefinition(
        feature_id="thrombocytopenia_severe", feature_name="Severe Thrombocytopenia",
        feature_type="PATTERN",
        feature_composition=["platelets_low"],
        calculation_method="platelets < 50",
        unit="K/uL",
        clinical_significance="high_bleeding_risk",
        description="Platelet count below 50 K/uL — clinically significant bleeding risk.",
    ),
    "hemolysis_pattern": FeatureDefinition(
        feature_id="hemolysis_pattern", feature_name="Hemolysis Pattern",
        feature_type="PATTERN",
        feature_composition=["hemoglobin_low", "reticulocytes_high"],
        clinical_significance="suggests_hemolysis",
        description="Low hemoglobin with high reticulocytes (compensatory) — if available.",
    ),
}


# ──────────────────────────────────────────────────────────────────────────────
# 5. ANEMIA SUBTYPE PATTERNS — specific anemia classifications
# ──────────────────────────────────────────────────────────────────────────────
ANEMIA_SUBTYPE_PATTERNS: Dict[str, FeatureDefinition] = {
    "iron_deficiency_anemia": FeatureDefinition(
        feature_id="iron_deficiency_anemia", feature_name="Iron Deficiency Anemia",
        feature_type="PATTERN",
        feature_composition=["hemoglobin_low", "mcv_low", "rdw_high"],
        clinical_significance="iron_deficiency_anemia",
        description="Classic IDA: microcytic, hypochromic anemia with high anisocytosis (RDW).",
    ),
    "vitamin_b12_deficiency": FeatureDefinition(
        feature_id="vitamin_b12_deficiency", feature_name="Vitamin B12 Deficiency Anemia",
        feature_type="PATTERN",
        feature_composition=["hemoglobin_low", "mcv_high", "rdw_high"],
        clinical_significance="b12_deficiency_megaloblastic_anemia",
        description="Macrocytic anemia with elevated RDW — megaloblastic (B12) pattern.",
    ),
    "folate_deficiency": FeatureDefinition(
        feature_id="folate_deficiency", feature_name="Folate Deficiency Anemia",
        feature_type="PATTERN",
        feature_composition=["hemoglobin_low", "mcv_high"],
        clinical_significance="folate_deficiency_megaloblastic_anemia",
        description="Macrocytic anemia (folate) — high MCV, often normal/high RDW.",
    ),
    "chronic_disease_anemia": FeatureDefinition(
        feature_id="chronic_disease_anemia", feature_name="Anemia of Chronic Disease",
        feature_type="PATTERN",
        feature_composition=["hemoglobin_low", "mcv_normal"],
        clinical_significance="anemia_of_chronic_disease",
        description="Normocytic (sometimes mildly microcytic) anemia with normal RDW.",
    ),
    "acute_blood_loss": FeatureDefinition(
        feature_id="acute_blood_loss", feature_name="Acute Blood Loss Anemia",
        feature_type="PATTERN",
        feature_composition=["hemoglobin_low", "mcv_normal"],
        clinical_significance="acute_hemorrhagic_anemia",
        description="Normocytic anemia of acute onset; reticulocytosis appears after a few days.",
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
    **ANEMIA_SUBTYPE_PATTERNS,
}


def get_feature(feature_id: str) -> FeatureDefinition:
    """
    Return the :class:`FeatureDefinition` for ``feature_id``.

    Raises
    ------
    KeyError
        If no feature with that id exists.
    """
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


def get_patterns() -> List[FeatureDefinition]:
    """Return all PATTERN features (disease patterns + anemia subtypes)."""
    return get_features_by_type("PATTERN")


def validate_feature_definition(fd: FeatureDefinition) -> bool:
    """
    Validate a feature definition's internal consistency.

    Checks, by type:
      * feature_id present; feature_type is one of VALID_FEATURE_TYPES
      * BINARY   — at least one of threshold_low / threshold_high is set
      * SEVERITY — severity_levels + threshold_ranges set; each band is named in
                   severity_levels and is a 2-tuple with min <= max
      * RATIO    — calculation_method set
      * PATTERN  — feature_composition (non-empty) or calculation_method set

    Returns
    -------
    bool
        True if the definition is well-formed.
    """
    if not fd.feature_id or not isinstance(fd.feature_id, str):
        return False
    if fd.feature_type not in VALID_FEATURE_TYPES:
        return False

    if fd.feature_type == "BINARY":
        return fd.threshold_low is not None or fd.threshold_high is not None

    if fd.feature_type == "SEVERITY":
        if not fd.severity_levels or not fd.threshold_ranges:
            return False
        for band, rng in fd.threshold_ranges.items():
            if band not in fd.severity_levels:
                return False
            if not (isinstance(rng, (tuple, list)) and len(rng) == 2):
                return False
            low, high = rng
            if low is None or high is None or low > high:
                return False
        return True

    if fd.feature_type == "RATIO":
        return bool(fd.calculation_method)

    if fd.feature_type == "PATTERN":
        return bool(fd.feature_composition) or bool(fd.calculation_method)

    return False


__all__ = [
    "FeatureDefinition",
    "VALID_FEATURE_TYPES",
    "BINARY_FEATURES",
    "SEVERITY_FEATURES",
    "COMPUTED_RATIO_FEATURES",
    "PATTERN_FEATURES",
    "ANEMIA_SUBTYPE_PATTERNS",
    "FEATURE_REGISTRY",
    "get_feature",
    "list_all_features",
    "get_features_by_type",
    "get_patterns",
    "validate_feature_definition",
]
