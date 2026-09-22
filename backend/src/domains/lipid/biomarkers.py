"""
Lipid-profile biomarker vocabulary — the single source of truth for this domain.

Everything panel-specific about *which biomarkers exist and how they're named*
lives here: canonical codes, name aliases (OCR-tolerant), LOINC codes, the graph
name bridge, fact↔biomarker mapping, and the required panel.

Naming note
-----------
``CODE_TO_NAME`` values double as the *base* of every Layer-3 feature id
(``ldl_cholesterol`` → ``ldl_cholesterol_high``) and as the key of the Layer-2
unit / quality tables, so they are snake_case and unique across all panels.
"""

from __future__ import annotations

from typing import Dict, List, Tuple, Union

# Minimum biomarkers required to run an analysis for this panel — the four
# directly-measured/derived values every lipid profile reports.
REQUIRED_BIOMARKERS: Tuple[str, ...] = ("CHOL", "LDL", "HDL", "TRIG")

# ── Name resolution ──────────────────────────────────────────────────────────
# Extracted name / synonym / code → canonical code (used for reference lookup).
NAME_TO_CODE: Dict[str, str] = {
    "total cholesterol": "CHOL", "cholesterol": "CHOL", "chol": "CHOL",
    "cholesterol total": "CHOL", "tc": "CHOL",
    "ldl": "LDL", "ldl cholesterol": "LDL", "ldl c": "LDL",
    "low density lipoprotein": "LDL",
    "hdl": "HDL", "hdl cholesterol": "HDL", "hdl c": "HDL",
    "high density lipoprotein": "HDL",
    "triglycerides": "TRIG", "triglyceride": "TRIG", "tg": "TRIG", "trig": "TRIG",
    "vldl": "VLDL", "vldl cholesterol": "VLDL",
    "very low density lipoprotein": "VLDL",
    "non hdl cholesterol": "NONHDL", "non hdl": "NONHDL", "nonhdl": "NONHDL",
    "chol hdl ratio": "CHOLHDL", "cholesterol hdl ratio": "CHOLHDL",
    "total cholesterol hdl ratio": "CHOLHDL",
}

# Canonical code → the lowercase name used by UnitConverter / DataQualityChecker
# and as the base of this panel's feature ids.
CODE_TO_NAME: Dict[str, str] = {
    "CHOL": "total_cholesterol",
    "LDL": "ldl_cholesterol",
    "HDL": "hdl_cholesterol",
    "TRIG": "triglycerides",
    "VLDL": "vldl_cholesterol",
    "NONHDL": "non_hdl_cholesterol",
    "CHOLHDL": "cholesterol_hdl_ratio",
}

# OCR-tolerant alias → code (Layer-1 adapter). Lowercase keys; fuzzy/de-spaced
# matching is layered on top of this in `layer1_adapter._resolve_biomarker_code`.
BIOMARKER_LOOKUP: Dict[str, str] = {
    "total cholesterol": "CHOL", "cholesterol total": "CHOL",
    "serum cholesterol": "CHOL", "cholesterol": "CHOL", "chol": "CHOL",
    "s cholesterol": "CHOL", "cholesterol serum": "CHOL",
    "ldl": "LDL", "ldl cholesterol": "LDL", "cholesterol ldl": "LDL",
    "ldl c": "LDL", "low density lipoprotein": "LDL",
    "low density lipoprotein cholesterol": "LDL", "ldl direct": "LDL",
    "hdl": "HDL", "hdl cholesterol": "HDL", "cholesterol hdl": "HDL",
    "hdl c": "HDL", "high density lipoprotein": "HDL",
    "high density lipoprotein cholesterol": "HDL",
    "triglycerides": "TRIG", "triglyceride": "TRIG", "serum triglycerides": "TRIG",
    "tg": "TRIG", "trig": "TRIG", "triglycerides serum": "TRIG",
    "vldl": "VLDL", "vldl cholesterol": "VLDL", "cholesterol vldl": "VLDL",
    "very low density lipoprotein": "VLDL",
    "non hdl cholesterol": "NONHDL", "non hdl": "NONHDL", "nonhdl": "NONHDL",
    "non hdl c": "NONHDL",
    "chol hdl ratio": "CHOLHDL", "cholesterol hdl ratio": "CHOLHDL",
    "total cholesterol hdl ratio": "CHOLHDL", "tc hdl ratio": "CHOLHDL",
    "chol hdl": "CHOLHDL",
}

# ── LOINC ────────────────────────────────────────────────────────────────────
# Canonical code → LOINC code (shared with the knowledge graph, whose Biomarker
# nodes carry the same `loinc_code`, so Layer 4 joins by LOINC rather than name).
CODE_TO_LOINC: Dict[str, str] = {
    "CHOL": "2093-3",      # Cholesterol [Mass/volume] in Serum or Plasma
    "LDL": "13457-7",      # Cholesterol.LDL [Mass/volume] (calculated)
    "HDL": "2085-9",       # Cholesterol.HDL [Mass/volume]
    "TRIG": "2571-8",      # Triglyceride [Mass/volume]
    "VLDL": "13458-5",     # Cholesterol.VLDL [Mass/volume] (calculated)
    "NONHDL": "43396-1",   # Cholesterol non HDL [Mass/volume]
    "CHOLHDL": "9830-1",   # Cholesterol.total/Cholesterol.in HDL [Mass ratio]
}

# ── Graph bridge (Layer 4) ───────────────────────────────────────────────────
# Binary/calculated fact id → canonical biomarker code(s).
FACT_TO_BIOMARKER: Dict[str, Union[str, List[str]]] = {
    "total_cholesterol_high": "CHOL", "total_cholesterol_low": "CHOL",
    "ldl_cholesterol_high": "LDL", "ldl_cholesterol_low": "LDL",
    "hdl_cholesterol_low": "HDL", "hdl_cholesterol_high": "HDL",
    "triglycerides_high": "TRIG", "triglycerides_low": "TRIG",
    "vldl_cholesterol_high": "VLDL",
    "non_hdl_cholesterol_high": "NONHDL",
    "cholesterol_hdl_ratio_high": "CHOLHDL",
    # calculated facts → biomarker combinations
    "chol_hdl_ratio": ["CHOL", "HDL"],
    "ldl_hdl_ratio": ["LDL", "HDL"],
    "trig_hdl_ratio": ["TRIG", "HDL"],
}

# Canonical code → lowercased graph Biomarker.name candidates (exact match to
# avoid substring collisions like "hdl" inside "non hdl cholesterol").
CODE_TO_GRAPH_NAMES: Dict[str, List[str]] = {
    "CHOL": ["total cholesterol", "cholesterol", "serum cholesterol",
             "cholesterol (total)"],
    "LDL": ["ldl", "ldl cholesterol", "low density lipoprotein",
            "low density lipoprotein cholesterol", "ldl-c"],
    "HDL": ["hdl", "hdl cholesterol", "high density lipoprotein",
            "high density lipoprotein cholesterol", "hdl-c"],
    "TRIG": ["triglycerides", "triglyceride", "serum triglycerides"],
    "VLDL": ["vldl", "vldl cholesterol", "very low density lipoprotein"],
    "NONHDL": ["non hdl cholesterol", "non-hdl cholesterol", "non-hdl-c"],
    "CHOLHDL": ["cholesterol/hdl ratio", "total cholesterol/hdl ratio",
                "chol/hdl ratio"],
}
