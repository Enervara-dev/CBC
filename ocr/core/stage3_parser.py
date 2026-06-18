"""
Stage 3 — Structured Parser
Converts raw tables and text into clean per-marker dicts.
Uses alias mapping from config to handle lab naming variation.
"""

import re
from typing import List, Dict, Any, Optional, Tuple


def parse(extraction: Dict[str, Any], config) -> List[Dict[str, Any]]:
    """
    Returns list of:
    {
        "parameter": canonical name,
        "value": float | None,
        "unit": str,
        "ref_low": float | None,
        "ref_high": float | None,
        "raw_text": str,
        "source": "table" | "text",
    }
    """
    results = []
    seen = set()

    # Try tables first (higher precision)
    for table in extraction.get("tables", []):
        for row in table:
            parsed = _parse_row(row, config)
            if parsed and parsed["parameter"] not in seen:
                seen.add(parsed["parameter"])
                results.append(parsed)

    # Fall back to raw text for any markers not yet found
    if extraction.get("raw_text"):
        text_results = _parse_text(extraction["raw_text"], config)
        for item in text_results:
            if item["parameter"] not in seen:
                seen.add(item["parameter"])
                results.append(item)

    return results


# ── Table row parsing ────────────────────────────────────────────────────────

def _parse_row(row: List[str], config) -> Optional[Dict[str, Any]]:
    """Try to parse a single table row into a marker dict."""
    row = [str(c).strip() for c in row if c is not None]
    if len(row) < 2:
        return None

    raw = " | ".join(row)
    canonical = _match_canonical(row[0], config)
    if not canonical:
        return None

    value = _extract_number(row[1]) if len(row) > 1 else None
    unit = _extract_unit(row[2]) if len(row) > 2 else ""
    ref_low, ref_high = _extract_reference(row[3] if len(row) > 3 else "")

    # If no ref range in row, use config defaults
    if ref_low is None and ref_high is None and canonical in config.REFERENCE_RANGES:
        ref_low, ref_high, unit_default = config.REFERENCE_RANGES[canonical]
        if not unit:
            unit = unit_default

    return {
        "parameter": canonical,
        "value": value,
        "unit": unit,
        "ref_low": ref_low,
        "ref_high": ref_high,
        "raw_text": raw,
        "source": "table",
    }


# ── Text parsing ─────────────────────────────────────────────────────────────

# Matches: "ALT (SGPT)   42   U/L   7-56"
# Also matches: "AST (SGOT)   <10   U/L   15.00-40.00"
# And:          "Total Protein   >12.00   g/dL   5.70-8.20"
_TEXT_PATTERN = re.compile(
    r"([A-Za-z][A-Za-z0-9 /().:\-]+?)"           # parameter name
    r"\s+"
    r"([<>≤≥]?\s*\d+\.?\d*)"                      # numeric value (with optional < > prefix)
    r"\s*"
    r"([A-Za-z/%]*)"                               # unit (optional)
    r"(?:\s+(\d+\.?\d*)\s*[-–]\s*(\d+\.?\d*))?",  # ref range X-Y (optional)
    re.IGNORECASE,
)

def _parse_text(text: str, config) -> List[Dict[str, Any]]:
    results = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _TEXT_PATTERN.match(line)
        if not m:
            continue
        name_raw, val_str, unit, ref_lo_str, ref_hi_str = m.groups()
        canonical = _match_canonical(name_raw.strip(), config)
        if not canonical:
            continue

        value = _extract_number(val_str)
        ref_low = float(ref_lo_str) if ref_lo_str else None
        ref_high = float(ref_hi_str) if ref_hi_str else None

        if ref_low is None and ref_high is None and canonical in config.REFERENCE_RANGES:
            ref_low, ref_high, unit_default = config.REFERENCE_RANGES[canonical]
            if not unit:
                unit = unit_default

        results.append({
            "parameter": canonical,
            "value": value,
            "unit": unit,
            "ref_low": ref_low,
            "ref_high": ref_high,
            "raw_text": line,
            "source": "text",
        })

    return results


# ── Helpers ──────────────────────────────────────────────────────────────────

# Terms that indicate a computed/derived row — should never be matched as a primary marker
_COMPUTED_ROW_KEYWORDS = {"RATIO", "INDEX", "CALCULATED", "DERIVED"}


def _match_canonical(raw: str, config) -> Optional[str]:
    normalized = raw.upper().strip()

    # Direct lookup first — this handles ratio markers like "A : G RATIO" that are
    # explicitly listed as aliases, before the computed-row keyword block fires.
    if normalized in config.PARAMETER_ALIASES:
        return config.PARAMETER_ALIASES[normalized]

    # Skip computed/ratio rows that aren't primary markers (e.g. "AST:ALT Ratio")
    if any(kw in normalized for kw in _COMPUTED_ROW_KEYWORDS):
        # Allow ratio aliases like "A:G RATIO" that are in the alias dict
        for alias, canonical in config.PARAMETER_ALIASES.items():
            if "RATIO" in alias and alias in normalized:
                return canonical
        return None

    # Word-boundary partial match: alias must appear as whole word(s) in the raw string
    # e.g. "SGPT (ALT)" → "ALT", but "BLOOD SUGAR FASTING" must not match "AST"
    import re as _re
    for alias, canonical in config.PARAMETER_ALIASES.items():
        pattern = r"(?<![A-Z])" + _re.escape(alias) + r"(?![A-Z])"
        if _re.search(pattern, normalized):
            return canonical
    return None


def _extract_number(s: str) -> Optional[float]:
    s = str(s).strip()
    # Handle "<10" → 9.9  (value is below detection limit, treat as just under)
    lt = re.match(r"^[<≤]\s*(\d+\.?\d*)", s)
    if lt:
        return round(float(lt.group(1)) * 0.99, 4)
    # Handle ">12.00" → 12.01  (value is above detection limit, treat as just over)
    gt = re.match(r"^[>≥]\s*(\d+\.?\d*)", s)
    if gt:
        return round(float(gt.group(1)) * 1.01, 4)
    m = re.search(r"\d+\.?\d*", s)
    return float(m.group()) if m else None


def _extract_unit(s: str) -> str:
    return re.sub(r"[^A-Za-z/%]", "", s).strip()


def _extract_reference(s: str) -> Tuple[Optional[float], Optional[float]]:
    s = str(s).strip()
    # "X - Y" or "X – Y"
    m = re.search(r"(\d+\.?\d*)\s*[-–]\s*(\d+\.?\d*)", s)
    if m:
        return float(m.group(1)), float(m.group(2))
    # "<X" or "≤X" → upper bound only, lower bound = 0
    lt = re.match(r"^[<≤]\s*(\d+\.?\d*)", s)
    if lt:
        return 0.0, float(lt.group(1))
    # ">X" or "≥X" → lower bound only, no upper bound
    gt = re.match(r"^[>≥]\s*(\d+\.?\d*)", s)
    if gt:
        return float(gt.group(1)), None
    return None, None
