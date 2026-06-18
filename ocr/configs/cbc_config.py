"""
CBC config placeholder — Krishna's module.
Fill in MARKERS, PARAMETER_ALIASES, REFERENCE_RANGES, CONDITIONS, CLINICAL_RULES,
SUMMARY_TEMPLATES following the same structure as lft_config.py.
The core pipeline will work unchanged with this config.
"""

REPORT_TYPE = "CBC"

MARKERS = [
    "HEMOGLOBIN", "RBC", "WBC", "PLATELETS",
    "HEMATOCRIT", "MCV", "MCH", "MCHC", "RDW",
    "NEUTROPHILS", "LYMPHOCYTES", "MONOCYTES", "EOSINOPHILS", "BASOPHILS",
]

PARAMETER_ALIASES = {
    "HEMOGLOBIN": "HEMOGLOBIN", "HB": "HEMOGLOBIN", "HGB": "HEMOGLOBIN",
    "RBC": "RBC", "RED BLOOD CELLS": "RBC", "RED CELL COUNT": "RBC",
    "WBC": "WBC", "WHITE BLOOD CELLS": "WBC", "LEUKOCYTES": "WBC", "TLC": "WBC",
    "PLATELETS": "PLATELETS", "PLT": "PLATELETS", "THROMBOCYTES": "PLATELETS",
    "HEMATOCRIT": "HEMATOCRIT", "HCT": "HEMATOCRIT", "PCV": "HEMATOCRIT",
    "MCV": "MCV", "MEAN CORPUSCULAR VOLUME": "MCV",
    "MCH": "MCH", "MEAN CORPUSCULAR HEMOGLOBIN": "MCH",
    "MCHC": "MCHC", "MEAN CORPUSCULAR HEMOGLOBIN CONCENTRATION": "MCHC",
    "RDW": "RDW", "RED CELL DISTRIBUTION WIDTH": "RDW",
    "NEUTROPHILS": "NEUTROPHILS", "NEUT": "NEUTROPHILS", "PMN": "NEUTROPHILS",
    "LYMPHOCYTES": "LYMPHOCYTES", "LYMPHS": "LYMPHOCYTES",
    "MONOCYTES": "MONOCYTES", "MONO": "MONOCYTES",
    "EOSINOPHILS": "EOSINOPHILS", "EOS": "EOSINOPHILS",
    "BASOPHILS": "BASOPHILS", "BASO": "BASOPHILS",
}

REFERENCE_RANGES = {
    "HEMOGLOBIN":   (13.5, 17.5, "g/dL"),
    "RBC":          (4.5,  5.9,  "million/uL"),
    "WBC":          (4.5,  11.0, "thousand/uL"),
    "PLATELETS":    (150,  400,  "thousand/uL"),
    "HEMATOCRIT":   (41,   53,   "%"),
    "MCV":          (80,   100,  "fL"),
    "MCH":          (27,   33,   "pg"),
    "MCHC":         (32,   36,   "g/dL"),
    "RDW":          (11.5, 14.5, "%"),
    "NEUTROPHILS":  (40,   70,   "%"),
    "LYMPHOCYTES":  (20,   40,   "%"),
    "MONOCYTES":    (2,    10,   "%"),
    "EOSINOPHILS":  (1,    6,    "%"),
    "BASOPHILS":    (0,    1,    "%"),
}

CRITICAL_THRESHOLDS = {
    "HEMOGLOBIN": (7.0,  None),
    "WBC":        (2.0,  30.0),
    "PLATELETS":  (50,   None),
}

CONDITIONS = [
    "Normal",
    "Iron Deficiency Anemia",
    "Microcytic Anemia",
    "Macrocytic Anemia",
    "Leukocytosis",
    "Leukopenia",
    "Thrombocytopenia",
    "Polycythemia",
]

CLINICAL_RULES = []  # TODO: Krishna implements CBC-specific rules

SEVERITY_BANDS = [
    (0.85, "Severe"),
    (0.65, "Moderate"),
    (0.40, "Mild"),
    (0.00, "Normal"),
]

SUMMARY_TEMPLATES = {}  # TODO: Krishna implements CBC summary templates
