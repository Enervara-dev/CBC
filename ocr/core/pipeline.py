"""
Core Pipeline — 7 stages, no ML model required.
Extraction: OCR-first — pdfplumber (digital) / PaddleOCR (scanned, image)
Reasoning:  Pure clinical rules
"""

import json
import sys
from typing import Dict, Any, Optional

from core import stage1_pdf_detector as s1
from core import stage2_extractor    as s2
from core import stage3_parser       as s3
from core import stage4_flagging     as s4
from core import stage5_rules        as s5
from core import stage6_clinical     as s6
from core import stage7_summary      as s7


def run(pdf_path: str, config=None) -> Dict[str, Any]:
    """
    Full pipeline: PDF → structured JSON + medical summary.

    Args:
        pdf_path:  Path to the PDF (or image) file.
        config:    Config module (lft_config, cbc_config, etc.).
                   Defaults to lft_config.
    """
    if config is None:
        from configs import lft_config as config

    result: Dict[str, Any] = {
        "report_type":       config.REPORT_TYPE,
        "pdf_path":          pdf_path,
        "pdf_type":          None,
        "page_count":        None,
        "extraction_method": None,
        "markers":           [],
        "conditions":        {},
        "top_condition":     None,
        "severity":          None,
        "summary":           {},
        "errors":            [],
    }

    # ── Stage 1: PDF type detection ──────────────────────────────────────────
    detection = s1.detect(pdf_path)
    result["pdf_type"]   = detection["type"]
    result["page_count"] = detection["page_count"]

    # ── Stage 2: Extraction (OCR-first: pdfplumber digital / PaddleOCR scanned) ──
    try:
        extraction = s2.extract(pdf_path, detection["type"])
    except Exception as e:
        result["errors"].append(str(e))
        return result

    result["extraction_method"] = extraction.get("method", "unknown")

    # ── Stage 3: Parse markers ───────────────────────────────────────────────
    parsed = s3.parse(extraction, config)
    if not parsed:
        result["errors"].append(
            "No recognizable markers found. Check PDF quality or report format."
        )
        return result

    # ── Stage 4: Flag each marker ────────────────────────────────────────────
    flagged = s4.flag(parsed, config)
    result["markers"] = flagged
    marker_dict = s4.build_marker_dict(flagged)

    # ── Stage 5: Rule-based condition detection ──────────────────────────────
    detection_result = s5.detect(marker_dict, config)

    # ── Stage 6: Clinical validation ─────────────────────────────────────────
    conditions = s6.validate(detection_result["conditions"], marker_dict, config)
    top_condition = detection_result["top_condition"]
    severity      = detection_result["severity"]
    top_features  = detection_result["top_features"]

    result["conditions"]    = conditions
    result["top_condition"] = top_condition
    result["severity"]      = severity

    # ── Stage 7: Summary ─────────────────────────────────────────────────────
    result["summary"] = s7.generate(
        flagged_markers=flagged,
        conditions=conditions,
        top_condition=top_condition,
        severity=severity,
        top_features=top_features,
        marker_dict=marker_dict,
        config=config,
    )

    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m core.pipeline <path_to_pdf> [LFT|CBC]")
        sys.exit(1)

    pdf = sys.argv[1]
    report_type = sys.argv[2].upper() if len(sys.argv) > 2 else "LFT"

    cfg = __import__(f"configs.{'lft' if report_type == 'LFT' else 'cbc'}_config",
                     fromlist=["*"])
    print(json.dumps(run(pdf, config=cfg), indent=2, default=str))
