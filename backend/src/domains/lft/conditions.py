"""
LFT clinical interpretation — what each abnormal liver result means, and what to do.

Structure mirrors ``domains/cbc/conditions.py``: a recommendation catalogue, a
per-biomarker table covering every parameter in both directions, and
multi-marker conditions keyed to ``validation.CLINICAL_VALIDATION_RULES``.

How the patterns are separated
------------------------------
Liver chemistry is read by *pattern*, and the pattern is quantitative. Layer 3
supplies the facts the rules need, each scaled to the patient's own upper limit
(ULN), so sex, age and pregnancy ranges carry through:

* R factor = (ALT / ULN) / (ALP / ULN): > 5 hepatocellular, < 2 cholestatic.
* ALP > 1.5 x ULN before calling cholestasis; a normal GGT sends a raised ALP to
  bone instead.
* AST / ALT > 2 for the alcohol pattern.
* ALT/AST >= 400 (10 x ULN) for acute hepatitis; with bilirubin > 2 x ULN,
  severe acute injury with a risk of failure (Hy's-law territory).

Clinical references
-------------------
* ACG Clinical Guideline "Evaluation of Abnormal Liver Chemistries" (2017); EASL.
* Albumin and bilirubin as markers of synthetic function: Child-Pugh components.
* ALT/AST > 1000 U/L narrows the differential sharply (ischaemic, toxic —
  notably paracetamol — and acute viral hepatitis), which is why it is critical.

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
    Rec("alcohol_drug_history", "Alcohol (AUDIT-C) and medication history, including "
        "over-the-counter and herbal products", "ACTION", priority=1),
    Rec("stop_hepatotoxins", "Review and stop potentially hepatotoxic drugs", "ACTION",
        priority=1),
    Rec("hepatitis_serology", "Viral hepatitis serology — HBsAg and anti-HCV (add hepatitis A "
        "and E IgM if acute)", "TEST", priority=2),
    Rec("liver_ultrasound", "Ultrasound of the liver and biliary tree", "TEST", priority=2),
    Rec("metabolic_screen_liver", "HbA1c and lipids — metabolic risk for fatty liver", "TEST",
        priority=3),
    Rec("fibrosis_assessment", "Fibrosis assessment — FIB-4 score, then elastography if "
        "indeterminate", "TEST", priority=2),
    Rec("lifestyle_liver", "Weight loss, exercise and alcohol reduction", "ACTION",
        priority=3),
    Rec("ck_if_muscle", "Creatine kinase if muscle injury is plausible", "TEST", priority=3),
    Rec("ast_alt_ratio", "Interpret with the AST:ALT ratio (above 2 favours alcohol)",
        "ACTION", priority=3),
    Rec("ggt_confirm", "GGT to confirm a raised ALP is hepatobiliary rather than bone",
        "TEST", priority=1),
    Rec("bone_profile", "Calcium, phosphate, vitamin D and PTH — bone source of ALP", "TEST",
        priority=2),
    Rec("alp_isoenzymes", "ALP isoenzymes if the source remains unclear", "TEST", priority=4),
    Rec("ama_pbc", "Anti-mitochondrial antibody if imaging is normal (primary biliary "
        "cholangitis)", "TEST", priority=3),
    Rec("alp_ggt_direct", "ALP and GGT to confirm a cholestatic pattern", "TEST", priority=2),
    Rec("interpret_with_alp", "Interpret alongside ALP", "ACTION", priority=3),
    Rec("zinc_mg_tft", "Zinc, magnesium and thyroid function", "TEST", priority=4),
    Rec("split_bilirubin", "Direct (conjugated) and indirect bilirubin fractions", "TEST",
        priority=1),
    Rec("haemolysis_screen", "Exclude haemolysis — full blood count, reticulocytes, LDH, "
        "haptoglobin and blood film", "TEST", priority=2),
    Rec("dat_test", "Direct antiglobulin (Coombs) test", "TEST", priority=2),
    Rec("haemolysis_drug_review", "Review drugs and G6PD status (oxidant drugs, antimalarials)",
        "ACTION", priority=3),
    Rec("reassure_gilbert", "Reassure — Gilbert syndrome needs no treatment or monitoring",
        "ACTION", priority=3),
    Rec("avoid_repeat_lfts", "Avoid repeated liver tests for this alone", "ACTION",
        priority=4),
    Rec("albumin_loss_inflammation", "Urine protein:creatinine ratio and CRP — kidney loss and "
        "inflammation also lower albumin", "TEST", priority=2),
    Rec("nutrition_assessment", "Nutritional assessment", "ACTION", priority=3),
    Rec("inr_platelets", "INR and platelet count — synthetic function and portal "
        "hypertension", "TEST", priority=1),
    Rec("rehydrate_repeat", "Rehydrate and repeat", "ACTION", priority=5),
    Rec("albumin_globulin_split", "Interpret with the albumin and globulin fractions",
        "ACTION", priority=3),
    Rec("spep", "Serum protein electrophoresis", "TEST", priority=2),
    Rec("autoimmune_screen", "Autoimmune liver screen (ANA, anti-smooth-muscle antibody, "
        "immunoglobulins) if the picture fits", "TEST", priority=3),
    Rec("immunoglobulins", "Quantify immunoglobulins", "TEST", priority=3),
    Rec("liver_assessment", "Assess for chronic liver disease", "ACTION", priority=2),
    Rec("surgical_referral", "Gastroenterology or surgical referral (MRCP/ERCP)", "REFERRAL",
        priority=1, urgency="urgent"),
    Rec("sepsis_check", "Assess now for cholangitis — fever, rigors, hypotension", "ACTION",
        priority=1, urgency="stat"),
    Rec("alcohol_support", "Offer alcohol cessation support", "REFERRAL", priority=2),
    Rec("hepatology_referral", "Hepatology referral", "REFERRAL", priority=1,
        urgency="urgent"),
    Rec("varices_screen", "Endoscopic variceal screening if cirrhosis is confirmed", "ACTION",
        priority=3),
    Rec("ebv_cmv", "EBV and CMV serology if hepatitis serology is negative", "TEST",
        priority=3),
    Rec("inr_glucose", "INR and glucose", "TEST", priority=1, urgency="stat"),
    Rec("paracetamol_level", "Paracetamol level and toxicology", "TEST", priority=1,
        urgency="stat"),
    Rec("hepatology_urgent", "Immediate hepatology / liver-unit discussion", "REFERRAL",
        priority=1, urgency="stat"),
    Rec("encephalopathy_check", "Assess for encephalopathy", "ACTION", priority=1,
        urgency="stat"),
]}


def _rec(key: str, **changes) -> Rec:
    """A catalogue recommendation, optionally with a per-use urgency or priority."""
    return replace(RECS[key], **changes) if changes else RECS[key]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Per-biomarker interpretation
# ─────────────────────────────────────────────────────────────────────────────
BIOMARKER_INTERPRETATIONS: Dict[str, Dict[str, Interp]] = {
    "ALT": {
        "high": Interp(
            finding_name="Raised ALT",
            meaning="ALT sits mostly inside hepatocytes, so a raised level means liver cells "
                    "are being damaged and releasing their contents. It is the more "
                    "liver-specific of the two transaminases.",
            consider=["Fatty liver disease (commonest)", "Alcohol", "Viral hepatitis B/C",
                      "Drug-induced injury — including paracetamol and statins",
                      "Autoimmune hepatitis", "Haemochromatosis", "Coeliac disease"],
            recommendations=[_rec("alcohol_drug_history"), _rec("hepatitis_serology"),
                             _rec("liver_ultrasound"), _rec("metabolic_screen_liver")],
            critical_note="A transaminase this high narrows the causes to ischaemic, toxic "
                          "(especially paracetamol) or acute viral hepatitis — assess the "
                          "same day and check the paracetamol level and INR.",
        ),
        "low": Interp(
            finding_name="Low ALT",
            meaning="A low ALT is not clinically significant in isolation.",
            consider=["Normal variant", "Occasionally B6 deficiency"],
            recommendations=[],
        ),
    },
    "AST": {
        "high": Interp(
            finding_name="Raised AST",
            meaning="AST is released by damaged liver cells but also by cardiac and skeletal "
                    "muscle, so it is less liver-specific than ALT. Its ratio to ALT is "
                    "informative.",
            consider=["Liver injury of any cause", "Alcohol (AST:ALT above 2)",
                      "Muscle injury or vigorous exercise", "Haemolysis",
                      "Myocardial injury"],
            recommendations=[_rec("ast_alt_ratio"), _rec("ck_if_muscle")],
            critical_note="Marked elevation warrants same-day assessment alongside ALT and "
                          "INR.",
        ),
        "low": Interp(
            finding_name="Low AST",
            meaning="Not clinically significant in isolation.",
            consider=["Normal variant"],
            recommendations=[],
        ),
    },
    "ALP": {
        "high": Interp(
            finding_name="Raised alkaline phosphatase",
            meaning="ALP comes from bile ducts, bone and placenta. A raised level is only "
                    "hepatobiliary if GGT is raised too — otherwise look to bone. Mild rises "
                    "(under 1.5 x the upper limit) are often non-specific.",
            consider=["Biliary obstruction — stones, stricture, tumour", "Cholestasis",
                      "Primary biliary cholangitis", "Bone disease — vitamin D deficiency, "
                      "Paget's disease, metastases, healing fracture", "Pregnancy (placental)",
                      "Normal growth in children and adolescents"],
            recommendations=[_rec("ggt_confirm")],
            critical_note="ALP this high needs prompt imaging of the biliary tree and "
                          "assessment for infiltrative disease.",
        ),
        "low": Interp(
            finding_name="Low alkaline phosphatase",
            meaning="An unusually low ALP is uncommon and occasionally points to a specific "
                    "deficiency or to hypophosphatasia.",
            consider=["Zinc or magnesium deficiency", "Hypothyroidism", "Malnutrition",
                      "Hypophosphatasia (rare)"],
            recommendations=[_rec("zinc_mg_tft")],
        ),
    },
    "GGT": {
        "high": Interp(
            finding_name="Raised GGT",
            meaning="GGT is sensitive to hepatobiliary disease and to enzyme induction. Its "
                    "main value is confirming that a raised ALP is coming from the liver; on "
                    "its own it is non-specific.",
            consider=["Alcohol", "Fatty liver", "Cholestasis or biliary obstruction",
                      "Enzyme-inducing drugs — phenytoin, carbamazepine, rifampicin"],
            recommendations=[_rec("alcohol_drug_history"), _rec("interpret_with_alp")],
        ),
        "low": Interp(
            finding_name="Low GGT",
            meaning="Not clinically significant.",
            consider=["Normal variant"],
            recommendations=[],
        ),
    },
    "TBIL": {
        "high": Interp(
            finding_name="Hyperbilirubinaemia",
            meaning="Bilirubin has risen above the reference range. Splitting it into "
                    "conjugated and unconjugated fractions is what localises the problem to "
                    "before, within, or after the liver. Jaundice becomes visible around "
                    "2.5–3 mg/dL.",
            consider=["Gilbert syndrome (common, benign, unconjugated)", "Haemolysis",
                      "Hepatocellular disease", "Biliary obstruction"],
            recommendations=[_rec("split_bilirubin"), _rec("haemolysis_screen")],
            critical_note="Jaundice at this level needs same-day assessment of liver function "
                          "and synthetic capacity (INR, albumin, encephalopathy).",
        ),
        "low": Interp(
            finding_name="Low bilirubin",
            meaning="Not clinically significant.",
            consider=["Normal variant"],
            recommendations=[],
        ),
    },
    "DBIL": {
        "high": Interp(
            finding_name="Raised direct (conjugated) bilirubin",
            meaning="The liver has conjugated the bilirubin but it is not reaching the gut — "
                    "which places the problem in the liver cells or the biliary tree. What "
                    "matters is its share of the total: a small rise with a mostly "
                    "unconjugated total (haemolysis, Gilbert) does not mean obstruction.",
            consider=["Biliary obstruction — stones, stricture, tumour",
                      "Hepatocellular disease", "Drug-induced cholestasis", "Sepsis"],
            recommendations=[_rec("liver_ultrasound"), _rec("alp_ggt_direct")],
            critical_note="Conjugated bilirubin this high suggests obstruction, which can "
                          "progress to cholangitis; assess the same day.",
        ),
        "low": Interp(
            finding_name="Low direct bilirubin",
            meaning="Not clinically significant.",
            consider=[],
            recommendations=[],
        ),
    },
    "IBIL": {
        "high": Interp(
            finding_name="Raised indirect (unconjugated) bilirubin",
            meaning="Bilirubin is being produced faster than the liver conjugates it, or "
                    "conjugation itself is reduced. When the direct fraction is also raised "
                    "this usually just accompanies liver or biliary disease; in isolation "
                    "with normal enzymes it is most often Gilbert syndrome.",
            consider=["Gilbert syndrome", "Haemolysis", "Resolving haematoma",
                      "Ineffective erythropoiesis"],
            recommendations=[_rec("haemolysis_screen")],
        ),
        "low": Interp(
            finding_name="Low indirect bilirubin",
            meaning="Not clinically significant.",
            consider=[],
            recommendations=[],
        ),
    },
    "ALB": {
        "low": Interp(
            finding_name="Hypoalbuminaemia",
            meaning="Albumin is made by the liver and falls with impaired synthesis, with "
                    "inflammation, and with protein loss. Because its half-life is about "
                    "three weeks, a low albumin points to a sustained problem rather than an "
                    "acute one.",
            consider=["Inflammation (albumin is a negative acute-phase reactant)",
                      "Chronic liver disease", "Nephrotic syndrome — check urine protein",
                      "Malnutrition or malabsorption", "Protein-losing enteropathy"],
            recommendations=[_rec("albumin_loss_inflammation"), _rec("nutrition_assessment")],
            critical_note="Albumin this low reflects significant synthetic failure or protein "
                          "loss and carries oedema and prognostic implications.",
        ),
        "high": Interp(
            finding_name="Raised albumin",
            meaning="Almost always haemoconcentration rather than increased production.",
            consider=["Dehydration"],
            recommendations=[_rec("rehydrate_repeat")],
        ),
    },
    "TP": {
        "low": Interp(
            finding_name="Low total protein",
            meaning="Reduced circulating protein, usually driven by the albumin fraction.",
            consider=["Liver disease", "Nephrotic syndrome", "Malnutrition",
                      "Protein-losing enteropathy"],
            recommendations=[_rec("albumin_globulin_split")],
        ),
        "high": Interp(
            finding_name="Raised total protein",
            meaning="Raised circulating protein, which is usually the globulin fraction and "
                    "occasionally a paraprotein.",
            consider=["Chronic inflammation or infection", "Dehydration",
                      "Multiple myeloma or MGUS"],
            recommendations=[_rec("spep")],
        ),
    },
    "GLOB": {
        "high": Interp(
            finding_name="Raised globulin",
            meaning="The non-albumin protein fraction is raised, reflecting immunoglobulin "
                    "production — chronic immune stimulation or a clonal process.",
            consider=["Chronic infection or inflammation", "Autoimmune hepatitis",
                      "Cirrhosis", "Myeloma or MGUS"],
            recommendations=[_rec("spep"), _rec("autoimmune_screen")],
        ),
        "low": Interp(
            finding_name="Low globulin",
            meaning="Reduced immunoglobulins, which may indicate an immune deficiency.",
            consider=["Immunodeficiency", "Protein loss", "Malnutrition"],
            recommendations=[_rec("immunoglobulins")],
        ),
    },
    "AGR": {
        "low": Interp(
            finding_name="Reversed albumin/globulin ratio",
            meaning="Albumin has fallen relative to globulin. This reversal is characteristic "
                    "of chronic liver disease and of chronic immune stimulation.",
            consider=["Cirrhosis", "Chronic inflammation or infection",
                      "Multiple myeloma", "Nephrotic syndrome"],
            recommendations=[_rec("spep"), _rec("liver_assessment")],
        ),
        "high": Interp(
            finding_name="Raised albumin/globulin ratio",
            meaning="Usually reflects low globulins rather than high albumin.",
            consider=["Immunodeficiency", "Genetic hypogammaglobulinaemia"],
            recommendations=[_rec("immunoglobulins")],
        ),
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# 2. Multi-marker conditions
# ─────────────────────────────────────────────────────────────────────────────
CLINICAL_CONDITIONS: Dict[str, Condition] = {
    "hepatocellular_injury": Condition(
        condition_id="hepatocellular_injury",
        threshold_defined=True,
        name="Hepatocellular injury pattern",
        meaning="Transaminases are raised out of proportion to ALP (R factor not in the "
                "cholestatic range), which places the injury in the liver cells themselves "
                "rather than the biliary tree.",
        consider=["Fatty liver disease", "Alcohol", "Viral hepatitis",
                  "Drug-induced liver injury", "Autoimmune hepatitis", "Haemochromatosis"],
        recommendations=[_rec("hepatitis_serology", priority=1), _rec("alcohol_drug_history"),
                         _rec("stop_hepatotoxins"), _rec("liver_ultrasound")],
        severity_from=["ALT", "AST"],
    ),
    "cholestasis": Condition(
        condition_id="cholestasis",
        threshold_defined=True,
        name="Cholestatic pattern",
        meaning="ALP is at least 1.5 x its upper limit and raised out of proportion to the "
                "transaminases (R factor below 5), with nothing suggesting a bone source — "
                "the pattern of impaired bile flow.",
        consider=["Gallstones", "Biliary stricture or tumour",
                  "Primary biliary or sclerosing cholangitis", "Drug-induced cholestasis",
                  "Infiltrative liver disease"],
        recommendations=[_rec("liver_ultrasound"), _rec("ggt_confirm"), _rec("ama_pbc")],
        severity_from=["ALP", "TBIL"],
    ),
    "isolated_alp_elevation": Condition(
        condition_id="isolated_alp_elevation",
        name="Raised ALP with normal GGT — bone source likely",
        meaning="GGT is normal, so the raised ALP is unlikely to be coming from the liver. "
                "Bone is the usual source, and the placenta in pregnancy.",
        consider=["Vitamin D deficiency / osteomalacia", "Paget's disease of bone",
                  "Bone metastases", "Healing fracture", "Hyperparathyroidism",
                  "Normal growth in adolescents", "Pregnancy (placental ALP)"],
        recommendations=[_rec("bone_profile", priority=1), _rec("alp_isoenzymes")],
        severity_from=["ALP"],
    ),
    "biliary_obstruction": Condition(
        condition_id="biliary_obstruction",
        severity_floor="urgent",
        name="Biliary obstruction (suspected)",
        meaning="A cholestatic ALP rise (at least 1.5 x the upper limit) with conjugated "
                "hyperbilirubinaemia — the pattern of an obstructed biliary tree, which needs "
                "imaging promptly.",
        consider=["Common bile duct stone", "Pancreatic or biliary malignancy",
                  "Stricture", "Cholangitis if febrile"],
        recommendations=[_rec("sepsis_check"), _rec("liver_ultrasound", urgency="urgent"),
                         _rec("surgical_referral")],
        severity_from=["TBIL", "ALP"],
        urgency="urgent",
        supersedes=["cholestasis"],
    ),
    "alcoholic_liver_disease": Condition(
        condition_id="alcoholic_liver_disease",
        name="Alcohol-related liver injury (suspected)",
        meaning="AST more than twice ALT is the classic alcohol-related pattern, "
                "particularly with a raised GGT. Cirrhosis of any cause and muscle injury can "
                "also raise the ratio, so it prompts a history rather than a label.",
        consider=["Alcohol-related hepatitis or fatty liver", "Alcohol-related cirrhosis",
                  "Cirrhosis of another cause", "Muscle injury (check creatine kinase)"],
        recommendations=[_rec("alcohol_drug_history"), _rec("alcohol_support"),
                         _rec("fibrosis_assessment")],
        severity_from=["AST", "ALT"],
        supersedes=["nafld_suspected"],
    ),
    "viral_hepatitis": Condition(
        condition_id="viral_hepatitis",
        threshold_defined=True,
        name="Acute hepatitis — viral, drug-induced or ischaemic",
        meaning="Transaminases at least 10 x their upper limit in a hepatocellular pattern "
                "indicate acute hepatitis. Viral hepatitis, drug or toxin injury (paracetamol "
                "above all) and ischaemic hepatitis are the main causes, separated by history, "
                "serology and the paracetamol level.",
        consider=["Acute hepatitis A, B or E", "Drug or toxin injury — paracetamol, "
                  "antituberculous drugs, herbal products",
                  "Ischaemic hepatitis (hypotension, heart failure)", "Autoimmune hepatitis",
                  "EBV or CMV"],
        recommendations=[_rec("hepatitis_serology", priority=1, urgency="urgent"),
                         _rec("paracetamol_level", urgency="urgent"),
                         _rec("inr_glucose", urgency="urgent"), _rec("stop_hepatotoxins"),
                         _rec("ebv_cmv")],
        severity_from=["ALT", "AST"],
        urgency="urgent",
        # "ALT high" is true from 40; acute hepatitis means >= 10 x the upper limit.
        min_severity="urgent",
        supersedes=["hepatocellular_injury", "nafld_suspected"],
    ),
    "nafld_suspected": Condition(
        condition_id="nafld_suspected",
        name="Fatty liver disease (suspected)",
        meaning="A modest, ALT-predominant rise without jaundice or a cholestatic pattern is "
                "most often metabolic fatty liver disease — the commonest liver abnormality in "
                "practice, and one that responds to metabolic risk-factor management. Other "
                "causes still need excluding.",
        consider=["Metabolic dysfunction-associated steatotic liver disease",
                  "Type 2 diabetes or insulin resistance", "Obesity", "Dyslipidaemia"],
        recommendations=[_rec("liver_ultrasound"), _rec("metabolic_screen_liver"),
                         _rec("fibrosis_assessment"), _rec("lifestyle_liver")],
        severity_from=["ALT"],
    ),
    "hepatic_synthetic_dysfunction": Condition(
        condition_id="hepatic_synthetic_dysfunction",
        name="Impaired hepatic synthetic function (possible)",
        meaning="An albumin below 2.5 g/dL raises the question of whether the liver's "
                "manufacturing capacity is failing — a different and more concerning axis than "
                "enzyme elevation. Inflammation and protein loss lower albumin too, and the "
                "INR answers the question.",
        consider=["Chronic liver disease or cirrhosis", "Sustained inflammation",
                  "Nephrotic syndrome", "Malnutrition"],
        recommendations=[_rec("inr_platelets", urgency="urgent"),
                         _rec("albumin_loss_inflammation"), _rec("fibrosis_assessment")],
        severity_from=["ALB"],
        urgency="urgent",
        # A mildly low albumin is usually inflammation, not liver failure.
        min_severity="urgent",
    ),
    "cirrhosis_suspected": Condition(
        condition_id="cirrhosis_suspected",
        severity_floor="urgent",
        name="Chronic liver disease / cirrhosis (suspected)",
        meaning="Low albumin with a reversed albumin/globulin ratio is the biochemical "
                "signature of established chronic liver disease.",
        consider=["Cirrhosis of any cause", "Chronic viral hepatitis",
                  "Alcohol-related liver disease", "Autoimmune hepatitis",
                  "Chronic inflammation or a paraprotein (also reverses the ratio)"],
        recommendations=[_rec("hepatology_referral"), _rec("inr_platelets"),
                         _rec("fibrosis_assessment"), _rec("varices_screen")],
        severity_from=["ALB", "TBIL"],
        urgency="urgent",
        supersedes=["hepatic_synthetic_dysfunction"],
    ),
    "unconjugated_hyperbilirubinemia": Condition(
        condition_id="unconjugated_hyperbilirubinemia",
        threshold_defined=True,
        name="Unconjugated hyperbilirubinaemia",
        meaning="Raised bilirubin that is predominantly unconjugated, pointing to increased "
                "production or reduced conjugation rather than to biliary disease.",
        consider=["Gilbert syndrome", "Haemolysis", "Resolving haematoma"],
        recommendations=[_rec("haemolysis_screen", priority=1)],
        severity_from=["TBIL"],
    ),
    "haemolysis_suspected": Condition(
        condition_id="haemolysis_suspected",
        name="Haemolysis (suspected) — anaemia with unconjugated hyperbilirubinaemia",
        meaning="Anaemia together with a raised, predominantly unconjugated bilirubin is the "
                "pattern of red cells being destroyed faster than they are made. It is not "
                "Gilbert syndrome while the haemoglobin is low, and it needs the haemolysis "
                "screen to confirm it and find the cause.",
        consider=["Autoimmune haemolytic anaemia", "G6PD deficiency (drug- or infection-"
                  "triggered)", "Hereditary spherocytosis", "Haemoglobinopathy (sickle cell, "
                  "thalassaemia)", "Malaria", "Microangiopathic haemolysis (TTP/HUS)",
                  "Ineffective erythropoiesis (B12/folate deficiency)"],
        recommendations=[_rec("haemolysis_screen", priority=1, urgency="urgent"),
                         _rec("dat_test", urgency="urgent"), _rec("haemolysis_drug_review")],
        severity_from=["HGB", "TBIL"],
        urgency="urgent",
        supersedes=["unconjugated_hyperbilirubinemia"],
    ),
    "gilbert_syndrome": Condition(
        condition_id="gilbert_syndrome",
        name="Gilbert syndrome (likely)",
        meaning="Isolated unconjugated hyperbilirubinaemia with normal liver enzymes and no "
                "anaemia is the picture of Gilbert syndrome — a benign inherited variant in "
                "around 5% of people. It needs recognition and reassurance, not treatment; "
                "haemolysis should be excluded once.",
        consider=["Gilbert syndrome", "Haemolysis must still be excluded once"],
        recommendations=[_rec("haemolysis_screen"), _rec("reassure_gilbert"),
                         _rec("avoid_repeat_lfts")],
        severity_from=["TBIL"],
        supersedes=["unconjugated_hyperbilirubinemia"],
    ),
    "acute_liver_failure_risk": Condition(
        condition_id="acute_liver_failure_risk",
        threshold_defined=True,
        name="Severe acute liver injury — risk of failure",
        meaning="Transaminases at least 10 x their upper limit with bilirubin above twice its "
                "upper limit indicate severe acute injury. Whether synthetic function is "
                "failing is now the question, and INR and mental state answer it.",
        consider=["Paracetamol or other toxin", "Ischaemic hepatitis",
                  "Acute viral hepatitis", "Autoimmune hepatitis"],
        recommendations=[_rec("inr_glucose"), _rec("paracetamol_level"),
                         _rec("hepatology_urgent"), _rec("encephalopathy_check")],
        severity_from=["ALT", "AST"],
        urgency="stat",
        # Graded on the transaminases, so obstructive jaundice with a modest ALT
        # does not read as liver failure.
        min_severity="urgent",
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
