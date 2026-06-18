"""
<PANEL> biomarker vocabulary — single source of truth for this domain.

Fill in every table below for your panel. Copy the structure from
``domains/cbc/biomarkers.py`` (the reference implementation) and replace the CBC
values with yours.
"""

from __future__ import annotations

from typing import Dict, List, Tuple, Union

# Codes that must be present to run an analysis for this panel.
REQUIRED_BIOMARKERS: Tuple[str, ...] = ()

# Extracted name / synonym / code → canonical code (for reference lookup).
NAME_TO_CODE: Dict[str, str] = {}

# Canonical code → the lowercase name used by UnitConverter / DataQualityChecker.
CODE_TO_NAME: Dict[str, str] = {}

# OCR-tolerant alias → code (Layer-1 adapter). Lowercase keys.
BIOMARKER_LOOKUP: Dict[str, str] = {}

# Canonical code → LOINC code (shared join key with the knowledge graph).
CODE_TO_LOINC: Dict[str, str] = {}

# Binary/calculated fact id → canonical biomarker code(s) (Layer-4 bridge).
FACT_TO_BIOMARKER: Dict[str, Union[str, List[str]]] = {}

# Canonical code → lowercased graph Biomarker.name candidates (exact match).
CODE_TO_GRAPH_NAMES: Dict[str, List[str]] = {}
