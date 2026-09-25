"""
CBC biomarker vocabulary — the single source of truth for this domain.

Everything panel-specific about *which biomarkers exist and how they're named*
lives here: canonical codes, name aliases (OCR-tolerant), LOINC codes, the graph
name bridge, fact↔biomarker mapping, and the required panel. The pipeline modules
import from here, so adding/adjusting a biomarker is a one-file edit.

To add a new specialty, copy this file into ``domains/<name>/biomarkers.py`` and
edit the tables (see ``domains/_template/``).
"""

from __future__ import annotations

from typing import Dict, List, Tuple, Union

# Minimum biomarkers required to run an analysis for this panel.
REQUIRED_BIOMARKERS: Tuple[str, ...] = ("HGB", "MCV", "RDW", "WBC", "PLT")

# ── Name resolution ──────────────────────────────────────────────────────────
# Extracted name / synonym / code → canonical code (used for reference lookup).
NAME_TO_CODE: Dict[str, str] = {
    "hemoglobin": "HGB", "hgb": "HGB", "hb": "HGB",
    "wbc": "WBC", "white blood cell count": "WBC", "leukocytes": "WBC", "tlc": "WBC",
    "rbc": "RBC", "red blood cell count": "RBC",
    "platelets": "PLT", "platelet": "PLT", "plt": "PLT", "platelet count": "PLT",
    "hematocrit": "HCT", "hct": "HCT", "pcv": "HCT", "packed cell volume": "HCT",
    "mcv": "MCV", "mch": "MCH", "mchc": "MCHC", "rdw": "RDW",
    "neutrophils": "NEUT", "neutrophil": "NEUT", "neut": "NEUT",
    "lymphocytes": "LYMPH", "lymphocyte": "LYMPH", "lymph": "LYMPH",
    "absolute neutrophil count": "ANC", "anc": "ANC",
    "absolute lymphocyte count": "ALC", "alc": "ALC",
}

# Canonical code → the lowercase name used by UnitConverter / DataQualityChecker.
CODE_TO_NAME: Dict[str, str] = {
    "HGB": "hemoglobin", "WBC": "wbc", "RBC": "rbc", "PLT": "platelets",
    "HCT": "hematocrit", "MCV": "mcv", "MCH": "mch", "MCHC": "mchc", "RDW": "rdw",
    "NEUT": "neutrophils", "LYMPH": "lymphocytes",
    "ANC": "absolute_neutrophils", "ALC": "absolute_lymphocytes",
}

# OCR-tolerant alias → code (Layer-1 adapter). Lowercase keys; fuzzy/de-spaced
# matching is layered on top of this in `layer1_adapter._resolve_biomarker_code`.
BIOMARKER_LOOKUP: Dict[str, str] = {
    "hemoglobin": "HGB", "haemoglobin": "HGB", "hgb": "HGB", "hb": "HGB",
    "hematocrit": "HCT", "haematocrit": "HCT", "hct": "HCT",
    "pcv": "HCT", "packed cell volume": "HCT",
    "rbc": "RBC", "rbc count": "RBC", "red blood cell": "RBC",
    "red blood cell count": "RBC", "red blood cells": "RBC", "erythrocytes": "RBC",
    "wbc": "WBC", "wbc count": "WBC", "white blood cell": "WBC",
    "white blood cell count": "WBC", "white blood cells": "WBC",
    "leukocytes": "WBC", "tlc": "WBC", "total leucocyte count": "WBC",
    "platelets": "PLT", "platelet": "PLT", "platelet count": "PLT",
    "plt": "PLT", "thrombocytes": "PLT",
    "mcv": "MCV", "mean corpuscular volume": "MCV", "mean cell volume": "MCV",
    "mch": "MCH", "mean corpuscular hemoglobin": "MCH", "mean cell hemoglobin": "MCH",
    # British spellings: Indian reports print "haemoglobin", and without these the
    # specific MCH/MCHC keys never matched — the label fell through to the generic
    # "haemoglobin" token and was recorded as HGB.
    "mean corpuscular haemoglobin": "MCH", "mean cell haemoglobin": "MCH",
    "mchc": "MCHC", "mean corpuscular hemoglobin concentration": "MCHC",
    "mean corpuscular haemoglobin concentration": "MCHC",
    "mean cell haemoglobin concentration": "MCHC",
    "mean cell hemoglobin concentration": "MCHC",
    "haemotocrit": "HCT", "haemotocrit(pcv)": "HCT",
    "rdw": "RDW", "red cell distribution width": "RDW",
    "red blood cell distribution width": "RDW", "rdw cv": "RDW",
    "neutrophils": "NEUT", "neutrophil": "NEUT", "neut": "NEUT",
    "lymphocytes": "LYMPH", "lymphocyte": "LYMPH", "lymph": "LYMPH",
    # Absolute counts are a different quantity from the percentages above (cells
    # per volume, not per 100 white cells) and must never resolve to NEUT/LYMPH.
    "absolute neutrophil count": "ANC", "absolute neutrophils": "ANC", "anc": "ANC",
    "absolute lymphocyte count": "ALC", "absolute lymphocytes": "ALC", "alc": "ALC",
}

# ── LOINC ────────────────────────────────────────────────────────────────────
# Canonical code → LOINC code (shared with the knowledge graph, whose Biomarker
# nodes carry the same `loinc_code`, so Layer 4 joins by LOINC rather than name).
# The first six are verified against the live graph; the rest are standard LOINC.
CODE_TO_LOINC: Dict[str, str] = {
    "HGB": "718-7", "HCT": "4544-3", "MCV": "787-2", "MCH": "785-6",
    "MCHC": "786-4", "PLT": "777-3", "WBC": "6690-2", "RBC": "789-8", "RDW": "788-0",
    "NEUT": "770-8", "LYMPH": "736-9",   # neutrophils/100 leukocytes, lymphocytes/100 leukocytes
    "ANC": "751-8", "ALC": "731-0",      # neutrophils, lymphocytes [#/volume] in blood
}

# ── Graph bridge (Layer 4) ───────────────────────────────────────────────────
# Binary/calculated fact id → canonical biomarker code(s).
FACT_TO_BIOMARKER: Dict[str, Union[str, List[str]]] = {
    "hemoglobin_low": "HGB", "hemoglobin_high": "HGB",
    "hematocrit_low": "HCT", "hematocrit_high": "HCT",
    "rbc_low": "RBC", "rbc_high": "RBC",
    "mcv_low": "MCV", "mcv_high": "MCV",
    "mch_low": "MCH", "mch_high": "MCH",
    "mchc_low": "MCHC", "mchc_high": "MCHC",
    "rdw_high": "RDW", "rdw_low": "RDW",
    "wbc_high": "WBC", "wbc_low": "WBC",
    "platelets_low": "PLT", "platelets_high": "PLT",
    "neutrophils_high": "NEUT", "neutrophils_low": "NEUT",
    "lymphocytes_low": "LYMPH", "lymphocytes_high": "LYMPH",
    "absolute_neutrophils_low": "ANC", "absolute_neutrophils_high": "ANC",
    "absolute_lymphocytes_low": "ALC", "absolute_lymphocytes_high": "ALC",
    # in-range facts some rules rely on (a normal RDW is evidence for thal trait)
    "mcv_normal": "MCV", "rdw_normal": "RDW",
    # calculated facts → biomarker combinations
    "mentzer_index": ["MCV", "RBC"],
    "mentzer_index_low": ["MCV", "RBC"],
    "mcv_xuln_high": "MCV",
    "rbc_index": ["HGB", "RBC"],
    "rdw_mcv_ratio": ["RDW", "MCV"],
}

# Canonical code → lowercased graph Biomarker.name candidates (exact match to
# avoid substring collisions like "mch" inside "mchc").
CODE_TO_GRAPH_NAMES: Dict[str, List[str]] = {
    "HGB": ["hemoglobin", "haemoglobin", "haemoglobin concentration",
            "hemoglobin concentration"],
    "HCT": ["hematocrit", "haematocrit", "haematocrit (hct)"],
    "RBC": ["rbc count", "red cell count", "red cells", "rbc",
            "red blood cell count"],
    "WBC": ["wbc", "white blood cells", "white blood cell count", "leucocyte differential"],
    "PLT": ["platelets", "platelet count"],
    "MCV": ["mcv", "mean corpuscular volume (mcv)", "mean corpuscular volume"],
    "MCH": ["mch", "mean corpuscular hemoglobin (mch)", "mean cell haemoglobin (mch)",
            "mean corpuscular haemoglobin", "mean cell haemoglobin"],
    "MCHC": ["mchc", "mean corpuscular hemoglobin concentration (mchc)",
             "mean corpuscular haemoglobin concentration (mchc)",
             "mean cell haemoglobin concentration"],
    "RDW": ["rdw", "rcdw", "red cell distribution width (rdw)", "red cell distribution width"],
    "NEUT": ["neutrophils", "neutrophil count"],
    "LYMPH": ["lymphocytes", "lymphocyte", "lymphocyte count"],
    "ANC": ["absolute neutrophil count"],
    "ALC": ["absolute lymphocyte count"],
}
