"""
Manual integration test for the OCR-first extraction stage.

Usage
-----
    python run_extraction_test.py                       # uses tests/sample_reports/image.png
    python run_extraction_test.py report.pdf            # digital PDF  -> pdfplumber
    python run_extraction_test.py scan.png              # image / scan -> PaddleOCR

What it does
------------
  stage1 detect (digital vs scanned) -> stage2 extract (OCR-first) and prints the
  reconstructed table rows + raw text. Then runs the full pipeline so you can see
  the parsed markers for the chosen report type.
"""

import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DEFAULT_IMAGE = os.path.join(HERE, "tests", "sample_reports", "image.png")


def _banner(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main():
    report_type = "CBC"
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IMAGE
    if len(sys.argv) > 2:
        report_type = sys.argv[2].upper()

    if not os.path.exists(path):
        print(f"ERROR: file not found: {path}")
        sys.exit(1)

    from core.stage1_pdf_detector import detect
    from core.stage2_extractor import extract

    _banner("INPUT")
    print(f"  file        : {path}")
    print(f"  report_type : {report_type}")

    det = detect(path)
    _banner("STAGE 1 — DETECTION")
    print(f"  type        : {det['type']}")
    print(f"  page_count  : {det['page_count']}")

    res = extract(path, det["type"])
    _banner("STAGE 2 — EXTRACTION")
    print(f"  method      : {res.get('method')}")
    print(f"  raw_text    : {len(res.get('raw_text', ''))} chars")

    _banner("RECONSTRUCTED TABLE ROWS")
    n = 0
    for table in res.get("tables", []):
        for row in table:
            print("  " + " | ".join(str(c) for c in row))
            n += 1
    if n == 0:
        print("  (no rows)")

    _banner("RAW TEXT (first 800 chars)")
    print(res.get("raw_text", "")[:800])

    # Parse + flag markers (stage3/4). We stop here — stage5-7 reasoning/summary
    # is the next phase and CBC summary templates are not implemented yet.
    from configs import cbc_config, lft_config
    config = cbc_config if report_type == "CBC" else lft_config
    from core import stage3_parser as s3, stage4_flagging as s4

    parsed = s3.parse(res, config)
    flagged = s4.flag(parsed, config)

    _banner(f"PARSED MARKERS ({report_type})  —  {len(flagged)} found")
    for m in flagged:
        print(f"  {m['parameter']:20} {str(m.get('value')):>10} {m.get('unit',''):10} "
              f"[{m.get('flag','')}]")
    if not flagged:
        print("  (none matched — check OCR output above)")

    print("\nDone.")


if __name__ == "__main__":
    main()
