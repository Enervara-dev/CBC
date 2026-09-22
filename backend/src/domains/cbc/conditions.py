"""
CBC clinical interpretation — what each abnormal result means, and what to do.

Three tables, consumed by the Layer-4 rule reasoner:

``RECS``
    The recommendation catalogue: one entry per action. Two findings that call
    for the same test reference the same entry, so the reasoner merges them by
    id — a single anaemic report used to list "Iron studies" six different ways.

``BIOMARKER_INTERPRETATIONS``
    Every CBC parameter, both directions. This is what guarantees a lone
    abnormal value is still explained and actioned — a haemoglobin of 6.2 does
    not need to form a recognised pattern to be reported as severe anaemia.

``CLINICAL_CONDITIONS``
    Named conditions inferred from several markers together. Keys match
    ``validation.CLINICAL_VALIDATION_RULES``, which supplies the matching logic
    (required / optional / contradictory findings, minimum confidence).

How the anaemia conditions relate
---------------------------------
The morphological reading — microcytic, normocytic, macrocytic — always fires
from haemoglobin and MCV. A specific cause *replaces* it (``supersedes``) only
when the indices genuinely discriminate:

* microcytic → iron deficiency (RDW raised, Mentzer >= 13) or thalassaemia
  trait (Mentzer < 13, RDW normal); when neither holds, "microcytic anaemia"
  stands with both in its differential;
* macrocytic → megaloblastic (MCV > 1.1 x upper limit); B12 and folate
  deficiency cannot be told apart on a blood count, so they are one reading;
* normocytic → anaemia of chronic disease is *not* separable on a blood count
  and is reported through normocytic anaemia's differential.

Clinical references
-------------------
* Anaemia definition and severity: WHO "Haemoglobin concentrations for the
  diagnosis of anaemia and assessment of severity" (2011/2024); restrictive
  transfusion threshold 7 g/dL (AABB 2023).
* Neutropenia grading and infection risk: CTCAE v5.0 / IDSA febrile-neutropenia
  guidance — graded on the absolute neutrophil count.
* Thrombocytopenia bleeding risk: ASH 2019.
* Microcytosis (Mentzer index), macrocytosis and polycythaemia work-up: BSH.

These are decision-support statements for a clinician, not diagnoses, and every
finding is emitted with the evidence that produced it.
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
    Rec("iron_studies", "Iron studies — ferritin (with CRP), transferrin saturation", "TEST",
        priority=2),
    Rec("b12_folate", "Serum B12 and folate (methylmalonic acid if B12 is borderline)", "TEST",
        priority=2),
    Rec("reticulocyte_count", "Reticulocyte count — separates reduced production from loss "
        "or destruction", "TEST", priority=3),
    Rec("hb_electrophoresis", "Haemoglobin electrophoresis / HPLC for thalassaemia trait",
        "TEST", priority=3),
    Rec("mentzer_review", "Check the Mentzer index (MCV / RBC) — below 13 suggests "
        "thalassaemia trait", "ACTION", priority=4),
    Rec("indices_review", "Interpret with haemoglobin and the red cell indices", "ACTION",
        priority=5),
    Rec("blood_film", "Peripheral blood film review", "TEST", priority=2),
    Rec("blood_film_spherocytes", "Blood film for spherocytes", "TEST", priority=3),
    Rec("repeat_artefact", "Repeat to exclude lipaemia or cold-agglutinin artefact", "TEST",
        priority=4),
    Rec("film_clumping", "Blood film to exclude platelet clumping (repeat in a citrate tube "
        "if seen)", "TEST", priority=1),
    Rec("coag_screen", "Coagulation screen if there is any bleeding", "TEST", priority=2),
    Rec("haem_referral_plt", "Haematology referral once the count is confirmed", "REFERRAL",
        priority=2, urgency="urgent"),
    Rec("avoid_antiplatelet", "Avoid antiplatelet agents, anticoagulants and intramuscular "
        "injections until reviewed", "ACTION", priority=2, urgency="urgent"),
    Rec("thrombocytosis_repeat", "Repeat after any acute illness settles — reactive "
        "thrombocytosis resolves", "TEST", priority=4),
    Rec("fever_advice", "Seek medical care the same day for any fever (neutropenic sepsis "
        "risk)", "ACTION", priority=1, urgency="urgent"),
    Rec("drug_review_marrow", "Review medications that can suppress the marrow — stop a "
        "likely culprit (clozapine, carbimazole) now if neutrophils are low", "ACTION",
        priority=2),
    Rec("hiv_test", "Consider HIV testing where the picture fits", "TEST", priority=3),
    Rec("repeat_after_illness", "Repeat the count after any acute illness settles", "TEST",
        priority=4),
    Rec("flow_cytometry", "Flow cytometry if an adult lymphocyte count stays above 5 x 10^9/L",
        "TEST", priority=3),
    Rec("anc_calc", "Obtain the total white cell count to calculate the absolute neutrophil "
        "count", "TEST", priority=2),
    Rec("alc_calc", "Obtain the total white cell count to calculate the absolute lymphocyte "
        "count", "TEST", priority=3),
    Rec("infection_source", "Look for a source of infection — history, examination, CRP",
        "TEST", priority=2),
    Rec("cultures", "Blood cultures before antibiotics if febrile or unwell", "TEST",
        priority=2),
    Rec("tsh_lft_macro", "Thyroid and liver function tests; review alcohol intake and drugs",
        "TEST", priority=3),
    Rec("repeat_hydrated", "Repeat when well hydrated to exclude haemoconcentration", "TEST",
        priority=3),
    Rec("secondary_polycythaemia", "Look for secondary causes — smoking, sleep apnoea, lung "
        "disease, testosterone use", "ACTION", priority=3),
    Rec("epo_jak2", "If persistent: serum erythropoietin and JAK2 V617F", "TEST", priority=3),
    Rec("haem_referral_pv", "Haematology referral if the haematocrit stays above 0.52 (men) / "
        "0.48 (women), or white cells or platelets are also raised", "REFERRAL", priority=2),
    Rec("gi_evaluation", "Investigate the source of iron loss — GI evaluation in men and "
        "post-menopausal women", "REFERRAL", priority=2, urgency="urgent"),
    Rec("coeliac_screen", "Coeliac serology", "TEST", priority=3),
    Rec("iron_replacement", "Iron replacement once the cause is being pursued", "ACTION",
        priority=3),
    Rec("avoid_empirical_iron", "Do not give iron unless iron studies show deficiency",
        "ACTION", priority=3),
    Rec("partner_screening", "If trait is confirmed, offer partner screening before pregnancy",
        "ACTION", priority=4),
    Rec("intrinsic_factor", "Intrinsic factor antibodies if B12 is low", "TEST", priority=3),
    Rec("neuro_exam", "Neurological examination — subacute combined degeneration", "ACTION",
        priority=2, urgency="urgent"),
    Rec("b12_before_folate", "Do not start folate alone until B12 deficiency is excluded",
        "ACTION", priority=1, urgency="urgent"),
    Rec("renal_inflam", "Renal function and CRP/ESR — chronic disease and kidney disease",
        "TEST", priority=2),
    Rec("haem_referral_pancytopenia", "Haematology referral — bone-marrow examination may be "
        "needed", "REFERRAL", priority=1, urgency="urgent"),
    Rec("urgent_film_review", "Blood film review today for blasts and abnormal cells", "TEST",
        priority=1, urgency="urgent"),
    Rec("haem_same_day", "Same-day haematology discussion", "REFERRAL", priority=1,
        urgency="urgent"),
]}


def _rec(key: str, **changes) -> Rec:
    """A catalogue recommendation, optionally with a per-use urgency or priority."""
    return replace(RECS[key], **changes) if changes else RECS[key]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Per-biomarker interpretation — every parameter, both directions
# ─────────────────────────────────────────────────────────────────────────────
BIOMARKER_INTERPRETATIONS: Dict[str, Dict[str, Interp]] = {
    "HGB": {
        "low": Interp(
            finding_name="Anaemia",
            meaning="Haemoglobin below the reference range means reduced oxygen-carrying "
                    "capacity. Symptoms track both the depth and the speed of the fall — a "
                    "slowly developing anaemia is often tolerated far better than an acute one.",
            consider=["Iron deficiency (commonest worldwide)", "B12 or folate deficiency",
                      "Occult or overt blood loss", "Anaemia of chronic disease",
                      "Haemolysis", "Bone-marrow disorder", "Chronic kidney disease"],
            recommendations=[_rec("iron_studies"), _rec("b12_folate"),
                             _rec("reticulocyte_count")],
            critical_note="Severe anaemia: assess symptoms and haemodynamic stability the "
                          "same day. Transfusion is generally considered below 7 g/dL (8 g/dL "
                          "with cardiovascular disease) or when symptomatic; otherwise treat "
                          "the cause.",
        ),
        "high": Interp(
            finding_name="Raised haemoglobin",
            meaning="Haemoglobin above the reference range is either a true increase in red "
                    "cell mass or haemoconcentration from reduced plasma volume.",
            consider=["Dehydration (commonest, and reversible)", "Chronic hypoxia — smoking, "
                      "sleep apnoea, lung disease, altitude", "Polycythaemia vera",
                      "Testosterone or erythropoietin use"],
            recommendations=[_rec("repeat_hydrated"), _rec("secondary_polycythaemia"),
                             _rec("epo_jak2")],
            critical_note="Marked erythrocytosis raises hyperviscosity and thrombosis risk; "
                          "seek haematology input.",
        ),
    },
    "HCT": {
        "low": Interp(
            finding_name="Low haematocrit",
            meaning="The proportion of blood volume occupied by red cells is reduced. "
                    "Haematocrit tracks haemoglobin closely and is interpreted alongside it.",
            consider=["Anaemia of any cause", "Fluid overload / dilution", "Acute blood loss"],
            recommendations=[_rec("indices_review")],
        ),
        "high": Interp(
            finding_name="Raised haematocrit",
            meaning="A raised red cell fraction, most often from reduced plasma volume rather "
                    "than increased red cell production.",
            consider=["Dehydration", "Chronic hypoxia", "Polycythaemia"],
            recommendations=[_rec("repeat_hydrated")],
            critical_note="A haematocrit this high carries hyperviscosity and thrombosis risk; "
                          "seek haematology advice.",
        ),
    },
    "RBC": {
        "low": Interp(
            finding_name="Low red cell count",
            meaning="Fewer circulating red cells. Interpreted with haemoglobin and the red "
                    "cell indices, which point to the mechanism.",
            consider=["Anaemia of any cause", "Marrow suppression", "Haemolysis", "Dilution"],
            recommendations=[_rec("indices_review")],
        ),
        "high": Interp(
            finding_name="Raised red cell count",
            meaning="More red cells than normal. With a low MCV this usually means many small "
                    "cells — thalassaemia trait — rather than polycythaemia; with normal "
                    "indices it may be relative (dehydration) or a true erythrocytosis.",
            consider=["Thalassaemia trait (when the MCV is low)", "Dehydration",
                      "Chronic hypoxia", "Polycythaemia vera"],
            recommendations=[_rec("mentzer_review")],
        ),
    },
    "MCV": {
        "low": Interp(
            finding_name="Microcytosis",
            meaning="Red cells are smaller than normal. With anaemia this is the single most "
                    "useful pointer to the cause, and it narrows the differential sharply.",
            consider=["Iron deficiency", "Thalassaemia trait", "Anaemia of chronic disease",
                      "Sideroblastic anaemia (uncommon)"],
            recommendations=[_rec("iron_studies"), _rec("hb_electrophoresis")],
        ),
        "high": Interp(
            finding_name="Macrocytosis",
            meaning="Red cells are larger than normal. Present with or without anaemia, and "
                    "worth explaining either way.",
            consider=["Alcohol excess", "B12 or folate deficiency", "Liver disease",
                      "Hypothyroidism", "Drugs (methotrexate, hydroxyurea, antiretrovirals)",
                      "Myelodysplasia", "Reticulocytosis"],
            recommendations=[_rec("b12_folate"), _rec("tsh_lft_macro")],
        ),
    },
    "MCH": {
        "low": Interp(
            finding_name="Low MCH (hypochromia)",
            meaning="Each red cell carries less haemoglobin than normal. Tracks microcytosis "
                    "and points to impaired haemoglobin synthesis.",
            consider=["Iron deficiency", "Thalassaemia trait", "Chronic disease"],
            recommendations=[_rec("iron_studies")],
        ),
        "high": Interp(
            finding_name="Raised MCH",
            meaning="More haemoglobin per red cell, which usually accompanies macrocytosis.",
            consider=["B12 or folate deficiency", "Alcohol excess", "Liver disease"],
            recommendations=[_rec("b12_folate")],
        ),
    },
    "MCHC": {
        "low": Interp(
            finding_name="Low MCHC",
            meaning="Reduced haemoglobin concentration within the red cell, seen in long-"
                    "standing iron deficiency and in thalassaemia.",
            consider=["Iron deficiency", "Thalassaemia"],
            recommendations=[_rec("iron_studies")],
        ),
        "high": Interp(
            finding_name="Raised MCHC",
            meaning="A genuinely high MCHC is uncommon and most often signals spherocytosis "
                    "or a measurement artefact.",
            consider=["Hereditary spherocytosis", "Autoimmune haemolysis",
                      "Lipaemia or cold agglutinins (artefact)"],
            recommendations=[_rec("blood_film_spherocytes"), _rec("repeat_artefact")],
        ),
    },
    "RDW": {
        "low": Interp(
            finding_name="Low RDW",
            meaning="Unusually uniform red cell size. Rarely of clinical significance on its own.",
            consider=["Usually a normal variant"],
            recommendations=[],
        ),
        "high": Interp(
            finding_name="Anisocytosis (raised RDW)",
            meaning="Red cell size varies more than normal, which usually means two "
                    "populations of cells — often an early or a mixed deficiency. RDW commonly "
                    "rises before the MCV moves, and a normal RDW with small cells favours "
                    "thalassaemia trait over iron deficiency.",
            consider=["Early iron deficiency", "Mixed deficiency (iron plus B12/folate)",
                      "Recent transfusion", "Response to treatment"],
            recommendations=[_rec("iron_studies"), _rec("b12_folate")],
        ),
    },
    "WBC": {
        "low": Interp(
            finding_name="Leukopenia",
            meaning="Fewer circulating white cells. The absolute neutrophil count, not the "
                    "total, determines the infection risk.",
            consider=["Viral infection", "Drug effect (chemotherapy, carbimazole, clozapine)",
                      "B12/folate deficiency", "Marrow disorder", "Hypersplenism",
                      "Autoimmune disease"],
            recommendations=[_rec("drug_review_marrow"), _rec("blood_film")],
            critical_note="A white count this low needs same-day review: check the "
                          "neutrophil count and treat any fever as neutropenic sepsis.",
        ),
        "high": Interp(
            finding_name="Leukocytosis",
            meaning="More circulating white cells, most often a reactive response to "
                    "infection, inflammation or stress. The differential shows which lineage.",
            consider=["Bacterial infection", "Inflammation or tissue injury",
                      "Corticosteroid therapy", "Physiological stress",
                      "Haematological malignancy if marked or persistent"],
            recommendations=[_rec("infection_source"), _rec("blood_film")],
            critical_note="A white count this high needs a same-day blood film and haematology "
                          "discussion to exclude leukaemia and leukostasis.",
        ),
    },
    "PLT": {
        "low": Interp(
            finding_name="Thrombocytopenia",
            meaning="A reduced platelet count. Bleeding with trauma or procedures becomes a "
                    "risk below about 50, and spontaneous bleeding mainly below 20 x 10^9/L; "
                    "the trend matters as much as the absolute value.",
            consider=["Spurious (EDTA clumping) — confirm on a film first", "Viral infection "
                      "(including dengue where endemic)", "Drug-induced", "ITP",
                      "Liver disease with hypersplenism", "Marrow disorder",
                      "TTP/DIC if unwell"],
            recommendations=[_rec("film_clumping"), _rec("coag_screen")],
            critical_note="At this level there is a real risk of spontaneous bleeding — "
                          "same-day haematology input, and no antiplatelet agents, "
                          "anticoagulants or intramuscular injections.",
        ),
        "high": Interp(
            finding_name="Thrombocytosis",
            meaning="A raised platelet count, usually reactive rather than clonal.",
            consider=["Reactive — infection, inflammation, iron deficiency, post-surgery",
                      "Post-splenectomy", "Essential thrombocythaemia if persistent"],
            recommendations=[_rec("thrombocytosis_repeat"), _rec("iron_studies")],
            critical_note="An extreme platelet count carries thrombotic and paradoxical "
                          "bleeding risk; seek haematology advice.",
        ),
    },
    "ANC": {
        "low": Interp(
            finding_name="Neutropenia",
            meaning="The absolute neutrophil count is below the reference range. Neutrophils "
                    "are the first defence against bacterial and fungal infection; the risk is "
                    "modest above 1.0, rises steeply below it, and is highest below "
                    "0.5 x 10^9/L (severe neutropenia).",
            consider=["Viral infection (usually transient)",
                      "Drug-induced — antithyroid drugs, clozapine, chemotherapy, some "
                      "antibiotics", "Benign ethnic neutropenia (common in African, Middle "
                      "Eastern and South Asian ancestry; usually 1.0–1.5)",
                      "B12/folate deficiency", "Autoimmune disease", "Marrow disorder"],
            recommendations=[_rec("fever_advice"), _rec("drug_review_marrow"),
                             _rec("blood_film"), _rec("repeat_after_illness")],
            critical_note="Severe neutropenia — any fever is neutropenic sepsis until proven "
                          "otherwise and needs same-day assessment and antibiotics.",
        ),
        "high": Interp(
            finding_name="Neutrophilia",
            meaning="The absolute neutrophil count is raised — the classic response to "
                    "bacterial infection, acute inflammation, corticosteroids or stress.",
            consider=["Bacterial infection", "Acute inflammation or tissue necrosis",
                      "Corticosteroids", "Smoking or physiological stress",
                      "Myeloproliferative disorder if persistent"],
            recommendations=[_rec("infection_source")],
            critical_note="A neutrophil count this high needs a blood film to separate a "
                          "leukaemoid reaction from a myeloproliferative disorder.",
        ),
    },
    "ALC": {
        "low": Interp(
            finding_name="Lymphopenia",
            meaning="The absolute lymphocyte count is reduced, reflecting impaired adaptive "
                    "immunity. Mild, transient lymphopenia during an acute illness is common.",
            consider=["Acute viral infection (including HIV)", "Corticosteroids",
                      "Autoimmune disease", "Malnutrition", "Recent chemotherapy"],
            recommendations=[_rec("hiv_test"), _rec("repeat_after_illness")],
            critical_note="Severe lymphopenia (below 0.5 x 10^9/L) raises the risk of "
                          "opportunistic infection; review the cause.",
        ),
        "high": Interp(
            finding_name="Lymphocytosis",
            meaning="The absolute lymphocyte count is raised — typically a viral infection in "
                    "younger patients. A count that stays above 5 x 10^9/L in an adult raises "
                    "chronic lymphocytic leukaemia.",
            consider=["Viral infection (EBV, CMV, hepatitis)", "Pertussis",
                      "Chronic lymphocytic leukaemia if persistent in an older adult"],
            recommendations=[_rec("blood_film"), _rec("flow_cytometry")],
            critical_note="A lymphocyte count this high needs a blood film and haematology "
                          "review.",
        ),
    },
    # Percentages are reported only when the absolute count is unavailable (no
    # WBC on the report): the reasoner suppresses them whenever ANC/ALC exist.
    "NEUT": {
        "low": Interp(
            finding_name="Low neutrophil percentage",
            meaning="Only the percentage is available, and a relative change can mislead: it "
                    "falls whenever another lineage rises. The absolute neutrophil count "
                    "(white count x percentage) decides whether this is neutropenia.",
            consider=["Relative change from raised lymphocytes", "True neutropenia if the "
                      "white count is also low"],
            recommendations=[_rec("anc_calc")],
        ),
        "high": Interp(
            finding_name="Raised neutrophil percentage",
            meaning="Only the percentage is available. A neutrophil predominance suggests a "
                    "bacterial or inflammatory response, but the absolute count confirms it.",
            consider=["Bacterial infection", "Inflammation", "Corticosteroids"],
            recommendations=[_rec("anc_calc")],
        ),
    },
    "LYMPH": {
        "low": Interp(
            finding_name="Low lymphocyte percentage",
            meaning="Only the percentage is available; it falls whenever neutrophils rise. "
                    "The absolute lymphocyte count decides whether this is lymphopenia.",
            consider=["Relative change from raised neutrophils", "True lymphopenia"],
            recommendations=[_rec("alc_calc")],
        ),
        "high": Interp(
            finding_name="Raised lymphocyte percentage",
            meaning="Only the percentage is available; it rises whenever neutrophils fall. "
                    "The absolute lymphocyte count decides whether this is lymphocytosis.",
            consider=["Relative change from low neutrophils", "Viral infection"],
            recommendations=[_rec("alc_calc")],
        ),
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# 2. Multi-marker conditions — keys match CLINICAL_VALIDATION_RULES
# ─────────────────────────────────────────────────────────────────────────────
CLINICAL_CONDITIONS: Dict[str, Condition] = {
    "iron_deficiency_anemia": Condition(
        condition_id="iron_deficiency_anemia",
        name="Iron deficiency anaemia (probable)",
        meaning="Low haemoglobin with small red cells, raised size variation (RDW) and a "
                "Mentzer index that does not suggest thalassaemia — the typical picture of "
                "iron deficiency, which a low ferritin confirms. In adults the priority is "
                "finding why iron was lost, not only replacing it.",
        consider=["Gastrointestinal blood loss (occult malignancy in older adults)",
                  "Heavy menstrual bleeding", "Malabsorption including coeliac disease",
                  "Dietary insufficiency", "Pregnancy"],
        recommendations=[_rec("iron_studies", priority=1), _rec("gi_evaluation"),
                         _rec("coeliac_screen"), _rec("iron_replacement")],
        severity_from=["HGB"],
        supersedes=["microcytic_anemia"],
    ),
    "thalassemia_trait": Condition(
        condition_id="thalassemia_trait",
        name="Thalassaemia trait (possible)",
        meaning="Small red cells with a relatively high red cell count (Mentzer index below "
                "13) and uniform cell size — the pattern of thalassaemia trait rather than "
                "iron deficiency. It is lifelong and needs no treatment; it matters because "
                "iron does not help and because of family planning.",
        consider=["Beta-thalassaemia trait", "Alpha-thalassaemia trait", "HbE trait",
                  "Coexisting iron deficiency (check ferritin)"],
        recommendations=[_rec("hb_electrophoresis", priority=1), _rec("iron_studies"),
                         _rec("avoid_empirical_iron"), _rec("partner_screening")],
        severity_from=["HGB"],
        supersedes=["microcytic_anemia"],
    ),
    "microcytic_anemia": Condition(
        condition_id="microcytic_anemia",
        threshold_defined=True,
        name="Microcytic anaemia",
        meaning="Anaemia with small red cells. Iron deficiency and thalassaemia trait are the "
                "two to separate — the treatment differs completely — and these indices do "
                "not clearly favour either.",
        consider=["Iron deficiency", "Thalassaemia trait", "Anaemia of chronic disease"],
        recommendations=[_rec("iron_studies", priority=1), _rec("hb_electrophoresis")],
        severity_from=["HGB"],
    ),
    "macrocytic_anemia": Condition(
        condition_id="macrocytic_anemia",
        threshold_defined=True,
        name="Macrocytic anaemia",
        meaning="Anaemia with enlarged red cells but an MCV below the megaloblastic range. At "
                "this degree alcohol, liver disease, hypothyroidism and drugs are as likely "
                "as B12 or folate deficiency.",
        consider=["Alcohol excess", "Liver disease", "B12 or folate deficiency",
                  "Hypothyroidism", "Drugs", "Reticulocytosis (bleeding, haemolysis)",
                  "Myelodysplasia"],
        recommendations=[_rec("b12_folate", priority=1), _rec("tsh_lft_macro"),
                         _rec("reticulocyte_count")],
        severity_from=["HGB"],
    ),
    "vitamin_b12_deficiency": Condition(
        condition_id="vitamin_b12_deficiency",
        name="Megaloblastic anaemia — B12 or folate deficiency (suspected)",
        meaning="Anaemia with an MCV above 110 fL is usually megaloblastic, and B12 and "
                "folate deficiency cannot be told apart on a blood count. B12 deficiency can "
                "cause irreversible nerve damage, so it is excluded promptly even when the "
                "anaemia is mild.",
        consider=["Pernicious anaemia", "Dietary deficiency (vegetarian or vegan diet)",
                  "Metformin or PPI use", "Malabsorption or ileal disease",
                  "Folate deficiency — diet, alcohol, pregnancy, methotrexate",
                  "Myelodysplasia if B12 and folate are normal"],
        recommendations=[_rec("b12_folate", priority=1, urgency="urgent"),
                         _rec("b12_before_folate"), _rec("neuro_exam"),
                         _rec("intrinsic_factor"), _rec("blood_film")],
        severity_from=["HGB"],
        urgency="urgent",
        supersedes=["folate_deficiency", "macrocytic_anemia"],
    ),
    "folate_deficiency": Condition(
        condition_id="folate_deficiency",
        name="Folate deficiency anaemia (suspected)",
        meaning="Megaloblastic anaemia consistent with folate deficiency. It is reported "
                "together with B12 deficiency, which must be excluded first: folate alone in "
                "B12 deficiency can precipitate neurological damage.",
        consider=["Dietary deficiency", "Alcohol excess", "Malabsorption", "Pregnancy",
                  "Methotrexate"],
        recommendations=[_rec("b12_folate", priority=1), _rec("b12_before_folate")],
        severity_from=["HGB"],
    ),
    "normocytic_anemia": Condition(
        condition_id="normocytic_anemia",
        threshold_defined=True,
        name="Normocytic anaemia",
        meaning="Anaemia with normal-sized red cells. The reticulocyte count is the most "
                "useful next test: it separates reduced production from loss or destruction. "
                "Anaemia of chronic disease, kidney disease and an early mixed deficiency "
                "cannot be separated on the blood count alone.",
        consider=["Anaemia of chronic disease or inflammation", "Chronic kidney disease",
                  "Acute blood loss", "Early or mixed deficiency", "Haemolysis",
                  "Marrow disorder"],
        recommendations=[_rec("reticulocyte_count", priority=1), _rec("renal_inflam"),
                         _rec("iron_studies"), _rec("b12_folate")],
        severity_from=["HGB"],
        supersedes=["chronic_disease_anemia"],
    ),
    "chronic_disease_anemia": Condition(
        condition_id="chronic_disease_anemia",
        name="Anaemia of chronic disease (suspected)",
        meaning="Mild to moderate anaemia in the pattern seen with sustained inflammation, "
                "where iron is present but not made available for erythropoiesis. It cannot "
                "be distinguished from other normocytic anaemias on a blood count, so it is "
                "reported through normocytic anaemia's differential.",
        consider=["Chronic infection", "Autoimmune or inflammatory disease", "Malignancy",
                  "Chronic kidney disease"],
        recommendations=[_rec("renal_inflam", priority=1), _rec("iron_studies")],
        severity_from=["HGB"],
    ),
    "acute_infection": Condition(
        condition_id="acute_infection",
        threshold_defined=True,
        name="Neutrophilic leukocytosis — infection or inflammation",
        meaning="A raised white count driven by neutrophils is the expected marrow response "
                "to bacterial infection or acute inflammation. Corticosteroids and "
                "physiological stress produce the same picture.",
        consider=["Bacterial infection", "Abscess", "Tissue necrosis or infarction",
                  "Post-operative inflammation", "Corticosteroid therapy"],
        recommendations=[_rec("infection_source", priority=1), _rec("cultures")],
        severity_from=["WBC", "ANC"],
    ),
    "immune_compromise": Condition(
        condition_id="immune_compromise",
        threshold_defined=True,
        name="Neutropenia",
        meaning="The absolute neutrophil count is below the reference range. Infection risk "
                "is graded on it (CTCAE): mild 1.0–1.5, moderate 0.5–1.0, severe below "
                "0.5 x 10^9/L — and a febrile patient with severe neutropenia is a medical "
                "emergency.",
        consider=["Viral infection (usually transient)",
                  "Drug-induced — antithyroid drugs, clozapine, chemotherapy",
                  "Benign ethnic neutropenia", "B12/folate deficiency",
                  "Autoimmune disease", "Marrow disorder"],
        recommendations=[_rec("fever_advice"), _rec("drug_review_marrow"),
                         _rec("blood_film"), _rec("b12_folate")],
        severity_from=["ANC"],
        urgency="urgent",
    ),
    "severe_thrombocytopenia": Condition(
        condition_id="severe_thrombocytopenia",
        name="Thrombocytopenia",
        meaning="Platelets below 50 x 10^9/L — the level at which bleeding with trauma or "
                "procedures becomes a real risk, with spontaneous bleeding mainly below 20. "
                "Excluding EDTA clumping comes first.",
        consider=["Spurious clumping", "ITP", "Drug-induced", "Viral infection including "
                  "dengue", "Liver disease", "TTP/DIC if the patient is unwell",
                  "Marrow disorder"],
        recommendations=[_rec("film_clumping", urgency="urgent"), _rec("haem_referral_plt"),
                         _rec("avoid_antiplatelet"), _rec("coag_screen")],
        severity_from=["PLT"],
        urgency="urgent",
        # "Platelets low" is true from 149; this condition means < 50.
        min_severity="urgent",
        threshold_defined=True,
    ),
    "pancytopenia": Condition(
        condition_id="pancytopenia",
        severity_floor="urgent",
        threshold_defined=True,
        name="Pancytopenia",
        meaning="All three cell lines — red cells, white cells and platelets — are reduced. "
                "That points to a shared cause, most often in the marrow, and warrants "
                "haematology input rather than three separate work-ups.",
        consider=["B12 or folate deficiency", "Marrow failure or infiltration (aplastic "
                  "anaemia, leukaemia, myelodysplasia)", "Hypersplenism (liver disease)",
                  "Drug-induced marrow suppression", "Severe infection (viral, TB, sepsis)"],
        recommendations=[_rec("blood_film", urgency="urgent", priority=1),
                         _rec("haem_referral_pancytopenia"), _rec("b12_folate"),
                         _rec("reticulocyte_count"), _rec("drug_review_marrow")],
        severity_from=["HGB", "ANC", "WBC", "PLT"],
        urgency="urgent",
    ),
    "marked_leukocytosis": Condition(
        condition_id="marked_leukocytosis",
        name="Marked leukocytosis — exclude haematological malignancy",
        meaning="A white count of 30 x 10^9/L or more is beyond the usual reactive range. A "
                "leukaemoid reaction to severe infection is possible, but leukaemia must be "
                "excluded — particularly with anaemia, low platelets or a lymphocyte-"
                "predominant count.",
        consider=["Leukaemoid reaction (severe infection, inflammation)",
                  "Chronic myeloid leukaemia", "Chronic lymphocytic leukaemia",
                  "Acute leukaemia"],
        recommendations=[_rec("urgent_film_review"), _rec("haem_same_day")],
        severity_from=["WBC"],
        urgency="urgent",
        min_severity="urgent",
        threshold_defined=True,
    ),
    "polycythemia": Condition(
        condition_id="polycythemia",
        threshold_defined=True,
        name="Polycythaemia (raised haemoglobin and haematocrit)",
        meaning="Haemoglobin and haematocrit are both raised. Once dehydration is excluded "
                "this is a true increase in red cell mass: secondary causes (hypoxia, smoking, "
                "testosterone) are commonest, and raised white cells or platelets as well "
                "point towards polycythaemia vera.",
        consider=["Dehydration (relative)", "Smoking or chronic hypoxia",
                  "Obstructive sleep apnoea", "Testosterone use", "Polycythaemia vera",
                  "Erythropoietin-secreting tumour (rare)"],
        recommendations=[_rec("repeat_hydrated", priority=1), _rec("secondary_polycythaemia"),
                         _rec("epo_jak2"), _rec("haem_referral_pv")],
        severity_from=["HGB", "HCT"],
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
