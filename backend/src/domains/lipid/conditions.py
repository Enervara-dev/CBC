"""
Lipid clinical interpretation — what each abnormal lipid result means, and what to do.

Structure mirrors the other panels: a recommendation catalogue, a per-biomarker
table covering every parameter, and multi-marker conditions keyed to
``validation.CLINICAL_VALIDATION_RULES``.

A note on how lipids differ from the other panels: the numbers are risk markers,
not disease markers. A raised LDL is not an illness — it is a modifiable
contributor to cardiovascular risk, and what to do about it depends on the
patient's total risk, not on the number alone. The interpretations below say so
rather than implying that every raised value needs treatment. Two exceptions are
genuinely urgent in their own right: triglycerides high enough to cause
pancreatitis, and an LDL high enough to suggest familial hypercholesterolaemia.

Where a condition only restates one measured value ("raised LDL cholesterol"),
it carries the same name as the per-biomarker finding, so the reasoner merges the
two into one observation instead of listing the same fact twice.

Clinical references
-------------------
* Risk categories and cut-points: NCEP ATP III; 2018 AHA/ACC/multisociety
  cholesterol guideline; ESC/EAS 2019 dyslipidaemia guidance.
* LDL >= 190 mg/dL as a familial hypercholesterolaemia trigger: AHA/ACC 2018.
* Triglycerides >= 500 mg/dL (severe) and >= 1000 mg/dL (pancreatitis risk):
  Endocrine Society 2012; AHA/ACC 2018.

Decision support for a clinician, not diagnoses; every finding carries evidence.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Dict

from domains.clinical_types import (
    BiomarkerInterpretation as Interp,
    ClinicalCondition as Condition,
    Recommendation as Rec,
)

# ─────────────────────────────────────────────────────────────────────────────
# 0. Recommendation catalogue — one entry per action
# ─────────────────────────────────────────────────────────────────────────────
RECS: Dict[str, Rec] = {rec.recommendation_id: rec for rec in [
    Rec("cv_risk_score", "Calculate 10-year cardiovascular risk (ASCVD/QRISK) and set the "
        "LDL target from it", "ACTION", priority=1),
    Rec("full_lipid_fractions", "Interpret with LDL, HDL and non-HDL rather than the total "
        "alone", "ACTION", priority=2),
    Rec("lifestyle_lipids", "Diet, exercise and smoking-cessation advice", "ACTION",
        priority=2),
    Rec("statin_consider", "Consider statin therapy according to the risk category",
        "ACTION", priority=2),
    Rec("secondary_causes", "Exclude secondary causes — TSH, HbA1c, urine protein, liver "
        "function", "TEST", priority=3),
    Rec("exercise_hdl", "Regular aerobic exercise — the most effective way to raise HDL",
        "ACTION", priority=2),
    Rec("smoking_cessation", "Smoking cessation", "ACTION", priority=2),
    Rec("metabolic_syndrome_screen", "Screen for diabetes and metabolic syndrome — HbA1c, "
        "waist circumference, blood pressure", "TEST", priority=2),
    Rec("fasting_repeat_tg", "Confirm on a fasting sample", "TEST", priority=1),
    Rec("alcohol_tg", "Review alcohol intake", "ACTION", priority=2),
    Rec("lifestyle_tg", "Reduce alcohol, refined carbohydrate and weight", "ACTION",
        priority=2),
    Rec("nonhdl_target", "Use non-HDL cholesterol as the treatment target when "
        "triglycerides are raised", "ACTION", priority=2),
    Rec("address_tg", "Address the underlying triglyceride elevation", "ACTION", priority=3),
    Rec("address_both", "Address both sides — lower LDL and raise HDL through exercise",
        "ACTION", priority=2),
    Rec("family_history", "Take a family history of premature cardiovascular disease",
        "ACTION", priority=2),
    Rec("lipid_clinic", "Lipid clinic referral", "REFERRAL", priority=1, urgency="urgent"),
    Rec("cascade_screening", "Cascade screening of first-degree relatives", "ACTION",
        priority=1, urgency="urgent"),
    Rec("dutch_criteria", "Apply Dutch Lipid Clinic Network criteria — tendon xanthomata, "
        "family history of premature CVD", "ACTION", priority=2),
    Rec("tg_lowering", "Start triglyceride-lowering treatment promptly (fibrate, very-low-fat "
        "diet) per local guidance", "ACTION", priority=1, urgency="urgent"),
    Rec("pancreatitis_symptoms", "Assess for abdominal pain — treat suspected pancreatitis "
        "as an emergency", "ACTION", priority=1, urgency="stat"),
    Rec("alcohol_abstinence", "Stop alcohol completely", "ACTION", priority=1),
    Rec("glycaemic_control", "Optimise glycaemic control", "ACTION", priority=1),
    Rec("investigate_low_chol", "Thyroid, liver and nutritional assessment if unexplained",
        "TEST", priority=4),
]}


def _rec(key: str, **changes) -> Rec:
    """A catalogue recommendation, optionally with a per-use urgency or priority."""
    return replace(RECS[key], **changes) if changes else RECS[key]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Per-biomarker interpretation
# ─────────────────────────────────────────────────────────────────────────────
BIOMARKER_INTERPRETATIONS: Dict[str, Dict[str, Interp]] = {
    "CHOL": {
        "high": Interp(
            finding_name="Raised total cholesterol",
            meaning="Total cholesterol above the desirable cut-point. On its own it is a "
                    "crude marker — the LDL and HDL fractions determine what it means, since "
                    "a high total driven by high HDL is not the same risk as one driven by "
                    "high LDL.",
            consider=["Dietary and lifestyle factors", "Familial hypercholesterolaemia",
                      "Hypothyroidism", "Nephrotic syndrome", "Cholestatic liver disease"],
            recommendations=[_rec("full_lipid_fractions"), _rec("cv_risk_score"),
                             _rec("secondary_causes")],
            critical_note="A cholesterol this high suggests a familial lipid disorder and "
                          "warrants specialist assessment.",
        ),
        "low": Interp(
            finding_name="Low total cholesterol",
            meaning="An unusually low cholesterol is rarely a target of treatment but can "
                    "point to an underlying illness.",
            consider=["Malnutrition", "Hyperthyroidism", "Liver disease",
                      "Chronic illness or malabsorption"],
            recommendations=[_rec("investigate_low_chol")],
        ),
    },
    "LDL": {
        "high": Interp(
            finding_name="Raised LDL cholesterol",
            meaning="LDL carries cholesterol into the arterial wall and is the primary "
                    "modifiable driver of atherosclerotic risk. It is also the main target of "
                    "lipid-lowering therapy — but the treatment threshold depends on the "
                    "patient's overall risk, not on this number alone.",
            consider=["Diet and lifestyle", "Familial hypercholesterolaemia",
                      "Hypothyroidism", "Nephrotic syndrome"],
            recommendations=[_rec("cv_risk_score"), _rec("lifestyle_lipids"),
                             _rec("statin_consider")],
            critical_note="An LDL at this level suggests familial hypercholesterolaemia — "
                          "screen first-degree relatives and refer to a lipid clinic.",
        ),
        "low": Interp(
            finding_name="Low LDL cholesterol",
            meaning="A low LDL is generally favourable and is the expected result of "
                    "effective lipid-lowering therapy.",
            consider=["On statin or other lipid-lowering therapy", "Malnutrition",
                      "Hyperthyroidism"],
            recommendations=[],
        ),
    },
    "HDL": {
        "low": Interp(
            finding_name="Low HDL cholesterol",
            meaning="HDL removes cholesterol from tissues, and a low level is an independent "
                    "cardiovascular risk factor. Unlike LDL it responds poorly to drugs — "
                    "exercise, smoking cessation and weight loss are what move it.",
            consider=["Metabolic syndrome and insulin resistance", "Physical inactivity",
                      "Smoking", "Obesity", "High triglyceride states"],
            recommendations=[_rec("exercise_hdl"), _rec("smoking_cessation"),
                             _rec("metabolic_syndrome_screen")],
            critical_note="An HDL this low is a strong independent risk marker and may "
                          "indicate a genetic HDL disorder or an artefact of very high "
                          "triglycerides.",
        ),
        "high": Interp(
            finding_name="Raised HDL cholesterol",
            meaning="A high HDL is generally protective. Very high values occasionally "
                    "reflect a genetic variant rather than benefit.",
            consider=["Regular exercise", "Moderate alcohol intake", "Genetic variant"],
            recommendations=[],
        ),
    },
    "TRIG": {
        "high": Interp(
            finding_name="Raised triglycerides",
            meaning="Triglycerides rise with insulin resistance, alcohol and a "
                    "carbohydrate-rich diet, and are strongly affected by whether the sample "
                    "was fasting. Moderate elevation is a risk marker; extreme elevation is a "
                    "direct danger to the pancreas.",
            consider=["Non-fasting sample — confirm before acting", "Insulin resistance or "
                      "type 2 diabetes", "Alcohol", "Obesity", "Hypothyroidism",
                      "Familial hypertriglyceridaemia"],
            recommendations=[_rec("fasting_repeat_tg"), _rec("metabolic_syndrome_screen"),
                             _rec("alcohol_tg")],
            critical_note="At this level there is a real risk of acute pancreatitis — this "
                          "needs urgent treatment in its own right, independent of "
                          "cardiovascular risk.",
        ),
        "low": Interp(
            finding_name="Low triglycerides",
            meaning="Not usually clinically significant.",
            consider=["Fasting state", "Malnutrition or malabsorption if persistent"],
            recommendations=[],
        ),
    },
    "VLDL": {
        "high": Interp(
            finding_name="Raised VLDL cholesterol",
            meaning="VLDL carries triglycerides and tracks them closely, so it rises for the "
                    "same reasons.",
            consider=["Hypertriglyceridaemia", "Insulin resistance", "Alcohol"],
            recommendations=[_rec("address_tg")],
        ),
        "low": Interp(
            finding_name="Low VLDL cholesterol",
            meaning="Not clinically significant.",
            consider=[],
            recommendations=[],
        ),
    },
    "NONHDL": {
        "high": Interp(
            finding_name="Raised non-HDL cholesterol",
            meaning="Non-HDL captures every atherogenic particle, not just LDL, so it is a "
                    "better risk marker than LDL when triglycerides are raised — and it does "
                    "not require a fasting sample.",
            consider=["Mixed dyslipidaemia", "Insulin resistance",
                      "Familial combined hyperlipidaemia"],
            recommendations=[_rec("nonhdl_target"), _rec("cv_risk_score")],
        ),
        "low": Interp(
            finding_name="Low non-HDL cholesterol",
            meaning="Favourable; the expected result of effective therapy.",
            consider=[],
            recommendations=[],
        ),
    },
    "CHOLHDL": {
        "high": Interp(
            finding_name="Raised cholesterol/HDL ratio",
            meaning="The ratio of total to HDL cholesterol summarises the balance between "
                    "atherogenic and protective particles in a single number, and predicts "
                    "risk better than either component alone.",
            consider=["Combined high LDL and low HDL", "Metabolic syndrome"],
            recommendations=[_rec("cv_risk_score"), _rec("address_both")],
        ),
        "low": Interp(
            finding_name="Low cholesterol/HDL ratio",
            meaning="A favourable ratio, indicating a protective lipid balance.",
            consider=[],
            recommendations=[],
        ),
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# 2. Multi-marker conditions
# ─────────────────────────────────────────────────────────────────────────────
CLINICAL_CONDITIONS: Dict[str, Condition] = {
    "hypercholesterolemia": Condition(
        condition_id="hypercholesterolemia",
        threshold_defined=True,
        name="Raised total cholesterol",
        meaning="Total cholesterol above the desirable range. What follows depends on the "
                "patient's overall cardiovascular risk and on the LDL and HDL fractions, "
                "rather than on the number itself.",
        consider=["Primary (diet, lifestyle, genetic)", "Hypothyroidism",
                  "Nephrotic syndrome", "Cholestatic liver disease"],
        recommendations=[_rec("cv_risk_score"), _rec("full_lipid_fractions"),
                         _rec("lifestyle_lipids"), _rec("secondary_causes")],
        severity_from=["CHOL"],
    ),
    "elevated_ldl": Condition(
        condition_id="elevated_ldl",
        threshold_defined=True,
        name="Raised LDL cholesterol",
        meaning="LDL above optimal — the primary modifiable target for reducing "
                "atherosclerotic cardiovascular risk. Whether to treat depends on the "
                "patient's overall risk category.",
        consider=["Diet and lifestyle", "Familial hypercholesterolaemia",
                  "Secondary causes — hypothyroidism, nephrotic syndrome"],
        recommendations=[_rec("cv_risk_score"), _rec("statin_consider"),
                         _rec("lifestyle_lipids")],
        severity_from=["LDL"],
    ),
    "familial_hypercholesterolemia_suspected": Condition(
        condition_id="familial_hypercholesterolemia_suspected",
        name="Familial hypercholesterolaemia (suspected)",
        meaning="An LDL of 190 mg/dL or more in an untreated patient suggests an inherited "
                "disorder rather than diet. It matters beyond this patient: first-degree "
                "relatives have a one-in-two chance of carrying it, and early treatment "
                "substantially changes their outcome.",
        consider=["Heterozygous familial hypercholesterolaemia",
                  "Severe polygenic hypercholesterolaemia", "Nephrotic syndrome",
                  "Hypothyroidism"],
        recommendations=[_rec("lipid_clinic"), _rec("cascade_screening"),
                         _rec("dutch_criteria"), _rec("secondary_causes")],
        severity_from=["LDL"],
        urgency="urgent",
        # LDL >= 190 mg/dL is the FH trigger; "LDL high" alone starts at 100.
        min_severity="urgent",
    ),
    "hypertriglyceridemia": Condition(
        condition_id="hypertriglyceridemia",
        threshold_defined=True,
        name="Raised triglycerides",
        meaning="Triglycerides above the normal range, usually reflecting insulin resistance, "
                "alcohol or diet. Confirm on a fasting sample before acting.",
        consider=["Non-fasting sample", "Insulin resistance or diabetes", "Alcohol",
                  "Obesity", "Hypothyroidism"],
        recommendations=[_rec("fasting_repeat_tg"), _rec("metabolic_syndrome_screen"),
                         _rec("lifestyle_tg")],
        severity_from=["TRIG"],
    ),
    "severe_hypertriglyceridemia": Condition(
        condition_id="severe_hypertriglyceridemia",
        name="Severe hypertriglyceridaemia — pancreatitis risk",
        meaning="Triglycerides of 500 mg/dL or more, with the pancreatitis risk rising "
                "steeply above 1000. This is a direct threat in its own right and is treated "
                "urgently, separately from any cardiovascular risk consideration.",
        consider=["Familial hypertriglyceridaemia", "Uncontrolled diabetes",
                  "Alcohol excess", "Drugs — oestrogens, retinoids, some antiretrovirals",
                  "Hypothyroidism"],
        recommendations=[_rec("pancreatitis_symptoms"), _rec("tg_lowering"),
                         _rec("alcohol_abstinence"), _rec("glycaemic_control"),
                         _rec("lipid_clinic")],
        severity_from=["TRIG"],
        urgency="urgent",
        # Pancreatitis risk begins around 500 mg/dL, not at the 150 cut-point.
        min_severity="urgent",
        threshold_defined=True,
    ),
    "low_hdl": Condition(
        condition_id="low_hdl",
        threshold_defined=True,
        name="Low HDL cholesterol",
        meaning="HDL below the protective threshold — an independent cardiovascular risk "
                "factor that responds to lifestyle far better than to drugs.",
        consider=["Metabolic syndrome", "Physical inactivity", "Smoking", "Obesity"],
        recommendations=[_rec("exercise_hdl"), _rec("smoking_cessation"),
                         _rec("metabolic_syndrome_screen")],
        severity_from=["HDL"],
    ),
    "atherogenic_dyslipidemia": Condition(
        condition_id="atherogenic_dyslipidemia",
        threshold_defined=True,
        name="Atherogenic dyslipidaemia",
        meaning="The combination of high triglycerides with low HDL is the lipid signature of "
                "insulin resistance and metabolic syndrome. It carries more cardiovascular "
                "risk than either abnormality alone, and it frequently precedes type 2 "
                "diabetes — which makes it worth acting on early.",
        consider=["Metabolic syndrome", "Insulin resistance or prediabetes",
                  "Central obesity", "Type 2 diabetes"],
        recommendations=[_rec("metabolic_syndrome_screen", priority=1),
                         _rec("lifestyle_tg", priority=1), _rec("nonhdl_target"),
                         _rec("cv_risk_score")],
        severity_from=["HDL"],
    ),
    "mixed_dyslipidemia": Condition(
        condition_id="mixed_dyslipidemia",
        threshold_defined=True,
        name="Mixed dyslipidaemia",
        meaning="Both LDL and triglycerides are raised — combined hyperlipidaemia, which "
                "carries substantial cardiovascular risk and often has a familial component.",
        consider=["Familial combined hyperlipidaemia", "Metabolic syndrome",
                  "Type 2 diabetes", "Hypothyroidism"],
        recommendations=[_rec("cv_risk_score"), _rec("nonhdl_target"),
                         _rec("family_history"), _rec("secondary_causes")],
        severity_from=["LDL"],
    ),
    "elevated_cardiovascular_risk": Condition(
        condition_id="elevated_cardiovascular_risk",
        threshold_defined=True,
        name="Elevated atherogenic risk profile",
        meaning="The cholesterol/HDL ratio is above the desirable level, indicating that "
                "atherogenic particles outweigh protective ones — a composite marker that "
                "predicts risk better than any single value.",
        consider=["Combined dyslipidaemia", "Metabolic syndrome"],
        recommendations=[_rec("cv_risk_score"), _rec("metabolic_syndrome_screen"),
                         _rec("lifestyle_lipids")],
        severity_from=["CHOLHDL"],
    ),
    "hypocholesterolemia": Condition(
        condition_id="hypocholesterolemia",
        threshold_defined=True,
        name="Low total cholesterol",
        meaning="An unusually low total cholesterol. Not a treatment target, but it can be a "
                "clue to an underlying illness worth identifying.",
        consider=["Malnutrition or malabsorption", "Hyperthyroidism",
                  "Advanced liver disease", "Chronic illness"],
        recommendations=[_rec("investigate_low_chol")],
        severity_from=["CHOL"],
    ),
}


def load_clinical_conditions() -> Dict[str, Condition]:
    """Return this panel's multi-marker condition catalogue."""
    return CLINICAL_CONDITIONS


def load_biomarker_interpretations() -> Dict[str, Dict[str, Interp]]:
    """Return this panel's per-biomarker interpretation table."""
    return BIOMARKER_INTERPRETATIONS


__all__ = [
    "RECS",
    "BIOMARKER_INTERPRETATIONS",
    "CLINICAL_CONDITIONS",
    "load_clinical_conditions",
    "load_biomarker_interpretations",
]
