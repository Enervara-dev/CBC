"""
LFT Config — single source of truth for all LFT knowledge.
No ML model needed. Conditions are detected entirely by clinical rules.
"""

REPORT_TYPE = "LFT"

MARKERS = [
    "ALT", "AST", "ALP", "GGT",
    "TOTAL_BILIRUBIN", "DIRECT_BILIRUBIN", "INDIRECT_BILIRUBIN",
    "TOTAL_PROTEIN", "ALBUMIN", "GLOBULIN", "AG_RATIO",
    "PT", "INR",
]

PARAMETER_ALIASES = {
    # ALT
    "ALT": "ALT", "SGPT": "ALT", "SGPT/ALT": "ALT",
    "ALANINE AMINOTRANSFERASE": "ALT", "ALANINE TRANSAMINASE": "ALT", "GPT": "ALT",
    # AST
    "AST": "AST", "SGOT": "AST", "SGOT/AST": "AST",
    "ASPARTATE AMINOTRANSFERASE": "AST", "ASPARTATE TRANSAMINASE": "AST", "GOT": "AST",
    # ALP
    "ALP": "ALP", "ALKALINE PHOSPHATASE": "ALP", "ALK PHOS": "ALP", "ALKPHOS": "ALP",
    # GGT
    "GGT": "GGT", "GAMMA GT": "GGT", "GAMMA-GT": "GGT",
    "GAMMA GLUTAMYL TRANSFERASE": "GGT", "GGTP": "GGT",
    # Bilirubin
    "TOTAL BILIRUBIN": "TOTAL_BILIRUBIN", "T. BILIRUBIN": "TOTAL_BILIRUBIN",
    "TBIL": "TOTAL_BILIRUBIN", "BILIRUBIN TOTAL": "TOTAL_BILIRUBIN",
    "DIRECT BILIRUBIN": "DIRECT_BILIRUBIN", "D. BILIRUBIN": "DIRECT_BILIRUBIN",
    "DBIL": "DIRECT_BILIRUBIN", "BILIRUBIN DIRECT": "DIRECT_BILIRUBIN",
    "CONJUGATED BILIRUBIN": "DIRECT_BILIRUBIN",
    "INDIRECT BILIRUBIN": "INDIRECT_BILIRUBIN", "I. BILIRUBIN": "INDIRECT_BILIRUBIN",
    "IBIL": "INDIRECT_BILIRUBIN", "BILIRUBIN INDIRECT": "INDIRECT_BILIRUBIN",
    "UNCONJUGATED BILIRUBIN": "INDIRECT_BILIRUBIN",
    # Protein
    "TOTAL PROTEIN": "TOTAL_PROTEIN", "T. PROTEIN": "TOTAL_PROTEIN",
    "PROTEIN TOTAL": "TOTAL_PROTEIN",
    "ALBUMIN": "ALBUMIN", "ALB": "ALBUMIN",
    "GLOBULIN": "GLOBULIN", "GLOB": "GLOBULIN",
    "A/G RATIO": "AG_RATIO", "AG RATIO": "AG_RATIO", "A:G RATIO": "AG_RATIO",
    "A : G RATIO": "AG_RATIO", "A:G": "AG_RATIO", "ALBUMIN/GLOBULIN": "AG_RATIO",
    "PROTEIN A/G RATIO": "AG_RATIO",
    # Coagulation
    "PT": "PT", "PROTHROMBIN TIME": "PT", "PROTHROMBIN": "PT",
    "INR": "INR", "INTERNATIONAL NORMALIZED RATIO": "INR",
}

# (low, high, unit)
REFERENCE_RANGES = {
    "ALT":               (7,    56,   "U/L"),
    "AST":               (10,   40,   "U/L"),
    "ALP":               (44,   147,  "U/L"),
    "GGT":               (9,    48,   "U/L"),
    "TOTAL_BILIRUBIN":   (0.2,  1.2,  "mg/dL"),
    "DIRECT_BILIRUBIN":  (0.0,  0.3,  "mg/dL"),
    "INDIRECT_BILIRUBIN":(0.2,  0.9,  "mg/dL"),
    "TOTAL_PROTEIN":     (6.0,  8.3,  "g/dL"),
    "ALBUMIN":           (3.5,  5.0,  "g/dL"),
    "GLOBULIN":          (2.0,  3.5,  "g/dL"),
    "AG_RATIO":          (1.1,  2.5,  ""),
    "PT":                (11.0, 13.5, "seconds"),
    "INR":               (0.8,  1.1,  ""),
}

CRITICAL_THRESHOLDS = {
    "ALT":             (None, 500),
    "AST":             (None, 500),
    "ALP":             (None, 1000),
    "GGT":             (None, 500),
    "TOTAL_BILIRUBIN": (None, 15.0),
    "DIRECT_BILIRUBIN":(None, 10.0),
    "ALBUMIN":         (2.0,  None),
    "PT":              (None, 20.0),
    "INR":             (None, 2.5),
}

CONDITIONS = [
    "Normal",
    "Fatty Liver",
    "Hepatitis",
    "Cholestasis",
    "Alcoholic Liver Disease",
    "Cirrhosis",
    "Acute Liver Failure",
]

# ── Helper ────────────────────────────────────────────────────────────────────

def _above(markers, key, multiplier=1.0):
    """True if marker is above (multiplier × ref_high)."""
    v = markers.get(key)
    if v is None or key not in REFERENCE_RANGES:
        return False
    return v > REFERENCE_RANGES[key][1] * multiplier

def _below(markers, key, multiplier=1.0):
    """True if marker is below (multiplier × ref_low)."""
    v = markers.get(key)
    if v is None or key not in REFERENCE_RANGES:
        return False
    ref_low = REFERENCE_RANGES[key][0]
    return v < ref_low * multiplier if ref_low > 0 else False

def _val(markers, key):
    return markers.get(key)

def _clamp(v):
    return max(0.0, min(1.0, v))

# ── Scoring rules — generate condition probabilities from marker values ────────
# Each rule receives the current markers dict and probs dict,
# adds/adjusts scores, and returns probs.

def _score_normal(markers, probs):
    """All markers within range → Normal = 1.0, everything else = 0."""
    any_abnormal = any(
        v is not None and key in REFERENCE_RANGES and
        (v < REFERENCE_RANGES[key][0] or v > REFERENCE_RANGES[key][1])
        for key, v in markers.items()
    )
    if not any_abnormal:
        probs = {c: 0.0 for c in CONDITIONS}
        probs["Normal"] = 1.0
    return probs

def _score_hepatitis(markers, probs):
    """
    Hepatitis: significant isolated ALT/AST elevation.
    ALT > 3× ULN is the key marker.
    """
    alt = _val(markers, "ALT")
    ast = _val(markers, "AST")
    alt_uln = REFERENCE_RANGES["ALT"][1]
    ast_uln = REFERENCE_RANGES["AST"][1]
    if alt is None and ast is None:
        return probs

    score = 0.0
    if alt and alt > alt_uln * 3:
        score = _clamp(0.4 + (alt - alt_uln * 3) / (alt_uln * 10))
    elif alt and alt > alt_uln:
        score = _clamp(0.2 + (alt - alt_uln) / (alt_uln * 5))

    if ast and ast > ast_uln * 2:
        score = max(score, _clamp(0.3 + (ast - ast_uln * 2) / (ast_uln * 8)))

    # Reduce if AST:ALT > 2 (more likely alcoholic)
    if alt and ast and alt > 0 and ast / alt >= 2.0:
        score *= 0.5

    probs["Hepatitis"] = max(probs.get("Hepatitis", 0), score)
    return probs

def _score_fatty_liver(markers, probs):
    """
    Fatty Liver (NAFLD): mildly elevated ALT/AST/GGT, AST:ALT < 1.
    Most common cause of mild transaminase elevation in asymptomatic patients.
    """
    alt = _val(markers, "ALT")
    ast = _val(markers, "AST")
    ggt = _val(markers, "GGT")
    alt_uln = REFERENCE_RANGES["ALT"][1]
    ast_uln = REFERENCE_RANGES["AST"][1]
    ggt_uln = REFERENCE_RANGES["GGT"][1]

    score = 0.0
    if alt and alt_uln < alt <= alt_uln * 3:
        score += 0.3
    if ast and ast_uln < ast <= ast_uln * 3:
        score += 0.2
    if ggt and ggt > ggt_uln:
        score += 0.15

    # Fatty liver: AST:ALT typically < 1
    if alt and ast and alt > 0:
        ratio = ast / alt
        if ratio < 1.0 and score > 0:
            score += 0.1
        elif ratio >= 2.0:
            score *= 0.3  # more likely alcoholic

    probs["Fatty Liver"] = max(probs.get("Fatty Liver", 0), _clamp(score))
    return probs

def _score_cholestasis(markers, probs):
    """
    Cholestasis: high ALP + high GGT + high direct bilirubin.
    ALP elevation is the hallmark.
    """
    alp = _val(markers, "ALP")
    ggt = _val(markers, "GGT")
    dbil = _val(markers, "DIRECT_BILIRUBIN")
    alp_uln = REFERENCE_RANGES["ALP"][1]
    ggt_uln = REFERENCE_RANGES["GGT"][1]
    dbil_uln = REFERENCE_RANGES["DIRECT_BILIRUBIN"][1]

    score = 0.0
    if alp and alp > alp_uln:
        score += _clamp(0.3 + (alp - alp_uln) / (alp_uln * 5))
    if ggt and ggt > ggt_uln:
        score += 0.2
    if dbil and dbil_uln and dbil > dbil_uln:
        score += 0.25

    probs["Cholestasis"] = max(probs.get("Cholestasis", 0), _clamp(score))
    return probs

def _score_alcoholic(markers, probs):
    """
    Alcoholic Liver Disease: AST:ALT > 2 is the classic ratio.
    GGT elevation strongly supports alcohol use.
    """
    alt = _val(markers, "ALT")
    ast = _val(markers, "AST")
    ggt = _val(markers, "GGT")
    ggt_uln = REFERENCE_RANGES["GGT"][1]

    score = 0.0
    if alt and ast and alt > 0:
        ratio = ast / alt
        if ratio >= 2.0:
            score += _clamp(0.4 + (ratio - 2.0) * 0.1)
        elif ratio >= 1.5:
            score += 0.2

    if ggt and ggt > ggt_uln * 2:
        score += 0.25
    elif ggt and ggt > ggt_uln:
        score += 0.1

    probs["Alcoholic Liver Disease"] = max(probs.get("Alcoholic Liver Disease", 0), _clamp(score))
    return probs

def _score_cirrhosis(markers, probs):
    """
    Cirrhosis: low albumin + elevated bilirubin + elevated PT/INR
    (impaired synthetic function). AG ratio often reversed.
    """
    alb = _val(markers, "ALBUMIN")
    tbil = _val(markers, "TOTAL_BILIRUBIN")
    inr = _val(markers, "INR")
    ag = _val(markers, "AG_RATIO")
    alb_low = REFERENCE_RANGES["ALBUMIN"][0]
    tbil_high = REFERENCE_RANGES["TOTAL_BILIRUBIN"][1]
    inr_high = REFERENCE_RANGES["INR"][1]

    score = 0.0
    if alb and alb < alb_low:
        score += _clamp(0.3 + (alb_low - alb) / alb_low)
    if tbil and tbil > tbil_high * 2:
        score += 0.2
    if inr and inr > inr_high * 1.3:
        score += 0.25
    if ag and ag < 1.0:
        score += 0.15

    probs["Cirrhosis"] = max(probs.get("Cirrhosis", 0), _clamp(score))
    return probs

def _score_acute_liver_failure(markers, probs):
    """
    Acute Liver Failure: very high transaminases + coagulopathy + low albumin.
    INR > 1.5 is part of the clinical definition.
    """
    alt = _val(markers, "ALT")
    ast = _val(markers, "AST")
    inr = _val(markers, "INR")
    alb = _val(markers, "ALBUMIN")
    tbil = _val(markers, "TOTAL_BILIRUBIN")
    alt_uln = REFERENCE_RANGES["ALT"][1]
    alb_low = REFERENCE_RANGES["ALBUMIN"][0]
    tbil_high = REFERENCE_RANGES["TOTAL_BILIRUBIN"][1]

    score = 0.0
    if alt and alt > alt_uln * 10:
        score += 0.35
    if ast and ast > REFERENCE_RANGES["AST"][1] * 10:
        score += 0.25
    if inr and inr > 1.5:
        score += 0.3
    if alb and alb < alb_low:
        score += 0.1
    if tbil and tbil > tbil_high * 3:
        score += 0.1

    probs["Acute Liver Failure"] = max(probs.get("Acute Liver Failure", 0), _clamp(score))
    return probs

def _validate_no_normal_with_disease(markers, probs):
    """Can't be Normal if any disease condition has meaningful score."""
    has_disease = any(
        probs.get(c, 0) >= 0.35
        for c in CONDITIONS if c != "Normal"
    )
    if has_disease:
        probs["Normal"] = 0.0
    return probs

def _validate_dominance(markers, probs):
    """
    If one condition dominates (> 0.6), suppress overlapping minor conditions
    to avoid confusing output.
    """
    top = max((c for c in CONDITIONS if c != "Normal"), key=lambda c: probs.get(c, 0))
    top_score = probs.get(top, 0)
    if top_score > 0.6:
        for c in CONDITIONS:
            if c != "Normal" and c != top and probs.get(c, 0) < 0.35:
                probs[c] = 0.0
    return probs


CLINICAL_RULES = [
    # Scoring — generate probabilities from marker values
    _score_normal,           # must run first — short-circuits if all normal
    _score_hepatitis,
    _score_fatty_liver,
    _score_cholestasis,
    _score_alcoholic,
    _score_cirrhosis,
    _score_acute_liver_failure,
    # Validation — cross-check and clean up
    _validate_no_normal_with_disease,
    _validate_dominance,
]

SEVERITY_BANDS = [
    (0.85, "Severe"),
    (0.65, "Moderate"),
    (0.40, "Mild"),
    (0.00, "Normal"),
]

SUMMARY_TEMPLATES = {
    "header": "Liver Function Test Analysis — Enervara Medical",

    "marker_high":         "{name} is elevated ({value} {unit}, reference: {ref_low}–{ref_high} {unit}).",
    "marker_low":          "{name} is below normal ({value} {unit}, reference: {ref_low}–{ref_high} {unit}).",
    "marker_normal":       "{name} is within normal range ({value} {unit}).",
    "marker_critical_high":"{name} is critically elevated ({value} {unit}) — immediate clinical attention recommended.",
    "marker_critical_low": "{name} is critically low ({value} {unit}) — immediate clinical attention recommended.",

    "condition_finding":   "{severity} {condition} detected (confidence: {probability:.0%}). Key contributors: {top_features}.",
    "normal_finding":      "All liver function markers are within acceptable limits. No hepatic pathology detected.",

    "ast_alt_note":        "AST:ALT ratio of {ratio:.1f} is {interpretation}.",
    "ast_alt_alcoholic":   "consistent with alcoholic liver injury (ratio ≥ 2)",
    "ast_alt_nonalcoholic":"consistent with non-alcoholic etiology (ratio < 2)",

    "recommendation_normal":   "No immediate follow-up required. Routine monitoring as per clinical guidelines.",
    "recommendation_mild":     "Clinical correlation recommended. Consider repeat LFT in 4–6 weeks.",
    "recommendation_moderate": "Prompt clinical evaluation advised. Additional investigations may be warranted.",
    "recommendation_severe":   "Urgent clinical review required. This report should be interpreted by a qualified physician immediately.",
}
