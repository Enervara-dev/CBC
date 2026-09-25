"""
LFT biomarker vocabulary — the single source of truth for this domain.

Everything panel-specific about *which biomarkers exist and how they're named*
lives here: canonical codes, name aliases (OCR-tolerant), LOINC codes, the graph
name bridge, fact↔biomarker mapping, and the required panel.

Naming note
-----------
``CODE_TO_NAME`` values double as the *base* of every Layer-3 feature id
(``albumin`` → ``albumin_low``) and as the key of the Layer-2 unit / quality
tables, so they are snake_case and unique across all registered panels.
"""

from __future__ import annotations

from typing import Dict, List, Tuple, Union

# Minimum biomarkers required to run an analysis for this panel. A liver panel
# without transaminases, ALP, bilirubin or albumin cannot be interpreted.
REQUIRED_BIOMARKERS: Tuple[str, ...] = ("ALT", "AST", "ALP", "TBIL", "ALB")

# ── Name resolution ──────────────────────────────────────────────────────────
# Extracted name / synonym / code → canonical code (used for reference lookup).
NAME_TO_CODE: Dict[str, str] = {
    "alt": "ALT", "sgpt": "ALT", "alanine aminotransferase": "ALT",
    "alt (sgpt)": "ALT", "sgpt (alt)": "ALT",
    "ast": "AST", "sgot": "AST", "aspartate aminotransferase": "AST",
    "ast (sgot)": "AST", "sgot (ast)": "AST",
    "alp": "ALP", "alkaline phosphatase": "ALP", "salp": "ALP",
    "ggt": "GGT", "gamma gt": "GGT", "gamma glutamyl transferase": "GGT",
    "total bilirubin": "TBIL", "bilirubin total": "TBIL", "tbil": "TBIL",
    "bilirubin": "TBIL",
    "direct bilirubin": "DBIL", "conjugated bilirubin": "DBIL", "dbil": "DBIL",
    "indirect bilirubin": "IBIL", "unconjugated bilirubin": "IBIL", "ibil": "IBIL",
    "total protein": "TP", "protein total": "TP", "tp": "TP",
    "albumin": "ALB", "alb": "ALB", "serum albumin": "ALB",
    "globulin": "GLOB", "glob": "GLOB",
    "a/g ratio": "AGR", "ag ratio": "AGR", "albumin globulin ratio": "AGR",
}

# Canonical code → the lowercase name used by UnitConverter / DataQualityChecker
# and as the base of this panel's feature ids.
CODE_TO_NAME: Dict[str, str] = {
    "ALT": "alt",
    "AST": "ast",
    "ALP": "alp",
    "GGT": "ggt",
    "TBIL": "total_bilirubin",
    "DBIL": "direct_bilirubin",
    "IBIL": "indirect_bilirubin",
    "TP": "total_protein",
    "ALB": "albumin",
    "GLOB": "globulin",
    "AGR": "ag_ratio",
}

# OCR-tolerant alias → code (Layer-1 adapter). Lowercase keys; fuzzy/de-spaced
# matching is layered on top of this in `layer1_adapter._resolve_biomarker_code`.
BIOMARKER_LOOKUP: Dict[str, str] = {
    "alt": "ALT", "sgpt": "ALT", "alanine aminotransferase": "ALT",
    "alanine transaminase": "ALT", "alt sgpt": "ALT", "sgpt alt": "ALT",
    "serum alanine aminotransferase": "ALT",
    "ast": "AST", "sgot": "AST", "aspartate aminotransferase": "AST",
    "aspartate transaminase": "AST", "ast sgot": "AST", "sgot ast": "AST",
    "serum aspartate aminotransferase": "AST",
    "alp": "ALP", "alkaline phosphatase": "ALP",
    "serum alkaline phosphatase": "ALP", "alk phos": "ALP", "alkp": "ALP",
    "ggt": "GGT", "ggtp": "GGT", "gamma gt": "GGT",
    "gamma glutamyl transferase": "GGT", "gamma glutamyl transpeptidase": "GGT",
    "total bilirubin": "TBIL", "bilirubin total": "TBIL", "tbil": "TBIL",
    "serum bilirubin total": "TBIL", "bilirubin": "TBIL", "s bilirubin": "TBIL",
    "direct bilirubin": "DBIL", "bilirubin direct": "DBIL", "dbil": "DBIL",
    "conjugated bilirubin": "DBIL",
    "indirect bilirubin": "IBIL", "bilirubin indirect": "IBIL", "ibil": "IBIL",
    "unconjugated bilirubin": "IBIL",
    "total protein": "TP", "protein total": "TP", "serum total protein": "TP",
    "total proteins": "TP",
    "albumin": "ALB", "serum albumin": "ALB", "alb": "ALB",
    "globulin": "GLOB", "serum globulin": "GLOB", "globulins": "GLOB",
    "a g ratio": "AGR", "ag ratio": "AGR", "albumin globulin ratio": "AGR",
    # NOTE: no bare "a g" alias. Multi-word aliases are substring-matched against
    # the whole OCR line, and "a g" occurs inside ordinary text — "plasma glucose"
    # contains it — which made every "Random Plasma Glucose 84" line resolve to a
    # nonsensical A/G ratio of 84. Keep multi-word aliases specific.
}

# ── LOINC ────────────────────────────────────────────────────────────────────
# Canonical code → LOINC code (shared with the knowledge graph, whose Biomarker
# nodes carry the same `loinc_code`, so Layer 4 joins by LOINC rather than name).
# Serum/plasma enzymatic-activity and mass-concentration codes.
CODE_TO_LOINC: Dict[str, str] = {
    "ALT": "1742-6",    # ALT [Enzymatic activity/volume] in Serum or Plasma
    "AST": "1920-8",    # AST [Enzymatic activity/volume] in Serum or Plasma
    "ALP": "6768-6",    # Alkaline phosphatase [Enzymatic activity/volume]
    "GGT": "2324-2",    # Gamma glutamyl transferase [Enzymatic activity/volume]
    "TBIL": "1975-2",   # Bilirubin.total [Mass/volume]
    "DBIL": "1968-7",   # Bilirubin.direct [Mass/volume]
    "IBIL": "1971-1",   # Bilirubin.indirect [Mass/volume]
    "TP": "2885-2",     # Protein [Mass/volume] in Serum or Plasma
    "ALB": "1751-7",    # Albumin [Mass/volume] in Serum or Plasma
    "GLOB": "2336-6",   # Globulin [Mass/volume] in Serum
    "AGR": "1759-0",    # Albumin/Globulin [Mass ratio] in Serum or Plasma
}

# ── Graph bridge (Layer 4) ───────────────────────────────────────────────────
# Binary/calculated fact id → canonical biomarker code(s).
FACT_TO_BIOMARKER: Dict[str, Union[str, List[str]]] = {
    "alt_high": "ALT", "alt_low": "ALT",
    "ast_high": "AST", "ast_low": "AST",
    "alp_high": "ALP", "alp_low": "ALP",
    "ggt_high": "GGT", "ggt_low": "GGT",
    "total_bilirubin_high": "TBIL", "total_bilirubin_low": "TBIL",
    "direct_bilirubin_high": "DBIL",
    "indirect_bilirubin_high": "IBIL",
    "total_protein_high": "TP", "total_protein_low": "TP",
    "albumin_low": "ALB", "albumin_high": "ALB",
    "globulin_high": "GLOB", "globulin_low": "GLOB",
    "ag_ratio_low": "AGR", "ag_ratio_high": "AGR",
    # calculated facts → biomarker combinations
    "de_ritis_ratio": ["AST", "ALT"],
    "albumin_globulin_ratio": ["ALB", "GLOB"],
    "conjugated_bilirubin_fraction": ["DBIL", "TBIL"],
    "alt_alp_ratio": ["ALT", "ALP"],
    # ratio facts (Layer 3 emits them when a ratio crosses its threshold)
    "de_ritis_ratio_high": ["AST", "ALT"],
    "r_factor_high": ["ALT", "ALP"], "r_factor_low": ["ALT", "ALP"],
    "alp_xuln_high": "ALP",
    "total_bilirubin_xuln_high": "TBIL",
    "conjugated_bilirubin_fraction_high": ["DBIL", "TBIL"],
    "conjugated_bilirubin_fraction_low": ["DBIL", "TBIL"],
    # in-range facts used as evidence (normal enzymes support Gilbert syndrome;
    # a normal GGT sends a raised ALP to bone)
    "alt_normal": "ALT", "ast_normal": "AST", "alp_normal": "ALP",
    "ggt_normal": "GGT", "direct_bilirubin_normal": "DBIL",
}

# Canonical code → lowercased graph Biomarker.name candidates (exact match to
# avoid substring collisions like "alt" inside "alt/alp ratio").
CODE_TO_GRAPH_NAMES: Dict[str, List[str]] = {
    "ALT": ["alt", "sgpt", "alanine aminotransferase",
            "alanine aminotransferase (alt)", "alanine transaminase"],
    "AST": ["ast", "sgot", "aspartate aminotransferase",
            "aspartate aminotransferase (ast)", "aspartate transaminase"],
    "ALP": ["alp", "alkaline phosphatase", "alkaline phosphatase (alp)",
            "serum alkaline phosphatase"],
    "GGT": ["ggt", "gamma glutamyl transferase",
            "gamma glutamyl transferase (ggt)", "gamma glutamyl transpeptidase"],
    "TBIL": ["total bilirubin", "bilirubin total", "bilirubin",
             "serum bilirubin (total)"],
    "DBIL": ["direct bilirubin", "conjugated bilirubin", "bilirubin direct"],
    "IBIL": ["indirect bilirubin", "unconjugated bilirubin", "bilirubin indirect"],
    "TP": ["total protein", "serum total protein", "total proteins"],
    "ALB": ["albumin", "serum albumin"],
    "GLOB": ["globulin", "serum globulin", "globulins"],
    "AGR": ["a/g ratio", "albumin globulin ratio", "albumin/globulin ratio"],
}
