"""
End-to-end CBC report analysis from a PDF/image file (all six layers).

    file → Layer 1 (PaddleOCR/pdfplumber) → adapter → L2 normalize → L3 features
         → L4 graph reasoning (Neo4j) → L5 validation → clinician-ready findings
         → Layer 6 (Gemini) patient + clinician reports + deterministic exports

Usage
-----
    python analyze_report.py path/to/report.pdf
    python analyze_report.py report.pdf --patient P001 --gender F --age 35
    python analyze_report.py scan.png --gender M --age 60 --out-dir ./out
    python analyze_report.py report.pdf --no-reports          # skip the LLM reports
    python analyze_report.py report.jpeg --show-ocr           # debug: what did OCR read?

Prerequisites (in the environment you run this from)
----------------------------------------------------
  - PaddleOCR + pdfplumber installed (Layer 1; same venv, no torch).
  - PostgreSQL reachable + seeded (Layer 2 reference ranges) — DATABASE_URL in CBC/.env.
  - Neo4j reachable (Layer 4) — NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD in CBC/.env.
  - GEMINI_API_KEY in CBC/.env + `pip install google-genai` (Layer 6 reports).
    Any layer that is unavailable degrades gracefully (status partial/error; the
    deterministic exports are always produced even if the LLM reports fail).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
# Layer 1 lives in ocr/ (PaddleOCRExtractor lazily imports core.* / preprocessing.*);
# Layers 2–5 live in backend/src. Put both on the path.
sys.path.insert(0, os.path.join(_ROOT, "ocr"))
sys.path.insert(0, os.path.join(_ROOT, "backend", "src"))


async def analyze(args: argparse.Namespace) -> int:
    # Layer 1
    from paddle_ocr_extractor import PaddleOCRExtractor
    # Layers 2–5 + orchestration
    from orchestration.cbc_file_analyzer import CBCFileAnalyzer
    from db.session import get_async_sessionmaker
    from services.normalization.normalizer import DataNormalizer
    from services.normalization.reference_lookup import ReferenceRangeLookup
    from services.normalization.unit_converter import UnitConverter
    from services.feature_generation.feature_definitions import FEATURE_REGISTRY
    from services.feature_generation.feature_generator import FeatureGenerator
    from services.graph_reasoning.neo4j_connection import Neo4jConnection
    from services.graph_reasoning.reasoning_engine import GraphReasoningEngine
    from services.confidence_validation.confidence_calibrator import ConfidenceCalibrator
    from services.confidence_validation.validation_engine import ConfidenceValidationEngine
    from services.confidence_validation.validation_rules import load_validation_rules

    if not os.path.isfile(args.file):
        print(f"ERROR: file not found: {args.file}")
        return 2

    # ── diagnostic: show what OCR read + how each name resolves ────────────────
    if args.show_ocr:
        return await _show_ocr(args.file)

    # ── wire the pipeline ────────────────────────────────────────────────────
    session = get_async_sessionmaker()()
    normalizer = DataNormalizer(
        db_session=session,
        unit_converter=UnitConverter,
        reference_lookup=ReferenceRangeLookup(session),
    )
    feature_gen = FeatureGenerator(FEATURE_REGISTRY)

    neo4j = Neo4jConnection()
    try:
        await neo4j.connect()
    except Exception as exc:  # noqa: BLE001 — degrade rather than abort
        print(f"WARN: Neo4j unavailable ({exc}); Layer 4 will be skipped (status=partial).")
    graph_reasoner = GraphReasoningEngine(neo4j)

    rules = load_validation_rules()
    validator = ConfidenceValidationEngine(ConfidenceCalibrator(rules), rules)

    analyzer = CBCFileAnalyzer(
        ocr_extractor=PaddleOCRExtractor(),
        layer2_normalizer=normalizer,
        layer3_feature_generator=feature_gen,
        layer4_graph_reasoner=graph_reasoner,
        layer5_validator=validator,
    )

    # ── run ──────────────────────────────────────────────────────────────────
    try:
        result = await analyzer.analyze_file(
            file_path=args.file,
            patient_id=args.patient,
            gender=args.gender,
            age_years=args.age,
            metadata={"source_file": os.path.basename(args.file)},
        )
    finally:
        await session.close()
        await neo4j.disconnect()

    # ── report ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(f"CBC ANALYSIS — {os.path.basename(args.file)}  (patient {args.patient})")
    print("=" * 70)
    print(f"status: {result.status}   findings: {len(result.final_findings)}   "
          f"time: {result.execution_time_ms} ms")
    if result.error_messages:
        print("notes:", "; ".join(result.error_messages))

    print("\nFINDINGS:")
    for f in result.final_findings:
        name = getattr(f, "finding_name", getattr(f, "finding_id", "?"))
        conf = getattr(f, "final_confidence", getattr(f, "confidence", ""))
        sev = getattr(f, "severity", "")
        print(f"  - {name}  (confidence {conf}, severity {sev})")
        for ev in (getattr(f, "source_evidence", None) or [])[:3]:
            print(f"      • {ev}")
    if not result.final_findings:
        print("  (none — see notes/OCR above)")

    flags = result.clinical_flags or {}
    flat = [c for v in flags.values() for c in v]
    if flat:
        print("\nCLINICAL FLAGS:")
        for c in flat:
            print(f"  [{getattr(c, 'flag_type', '?')}] {getattr(c, 'message', '')}")

    # ── Layer 6: presentation reports (Gemini) + deterministic exports ─────────
    bundle = None
    if args.reports and result.final_findings:
        from services.presentation.llm_client import GeminiLLMClient
        from services.presentation.report_generator import ReportGenerator

        llm = GeminiLLMClient(model=args.model) if args.model else GeminiLLMClient()
        bundle = await ReportGenerator(llm).generate(
            final_findings=result.final_findings,
            clinical_flags=result.clinical_flags,
            recommendations=result.recommendations,
            audit_trail=result.audit_trail.layer5_validation,
            patient_id=args.patient,
            timestamp=result.timestamp,
        )
        if bundle.patient_report.generated:
            print("\n" + "=" * 70 + "\nPATIENT REPORT\n" + "=" * 70 + "\n" + bundle.patient_report.report_text)
        if bundle.clinician_report.generated:
            print("\n" + "=" * 70 + "\nCLINICIAN REPORT\n" + "=" * 70 + "\n" + bundle.clinician_report.report_text)
        if bundle.warnings:
            print("\nreport notes:", "; ".join(bundle.warnings))
    elif args.reports:
        print("\n(no findings — skipping Layer 6 report generation)")

    # ── outputs ────────────────────────────────────────────────────────────────
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(result.to_json())
        print(f"\nFull analysis JSON written to {args.json}")
    if args.out_dir:
        _write_outputs(args.out_dir, result, bundle)
        print(f"Reports + exports written to {args.out_dir}/")

    print()
    return 0 if result.status in ("success", "partial") else 1


async def _show_ocr(file_path: str) -> int:
    """Diagnostic: raw OCR detections + clustered rows + name→code resolution."""
    from paddle_ocr_extractor import PaddleOCRExtractor
    from orchestration.layer1_adapter import _resolve_biomarker_code

    ex = PaddleOCRExtractor()
    try:
        raw = await asyncio.to_thread(ex._run_ocr, file_path)
    except Exception as exc:  # noqa: BLE001
        print(f"OCR extraction failed: {exc}")
        return 1

    def yc(it: dict) -> float:
        b = it.get("bbox") or [0, 0, 0, 0]
        return (b[1] + b[3]) / 2.0

    # 1) raw detections — shows whether each label/value was detected, and at what y
    raw_sorted = sorted(raw, key=lambda it: (yc(it), (it.get("bbox") or [0])[0]))
    print(f"\nRAW OCR — {len(raw)} text detections (text | x , y | conf):")
    print("-" * 78)
    for it in raw_sorted:
        b = it.get("bbox") or [0, 0, 0, 0]
        print(f"  {str(it.get('text',''))[:40]:40} | x{int(b[0]):>4},y{int(yc(it)):>4} "
              f"| {float(it.get('confidence', 0) or 0):.2f}")

    # 2) clustered rows → parsed biomarkers → resolution
    rows = ex._extract_table_from_ocr_output(raw)
    print(f"\nCLUSTERED ROWS → biomarkers ({len(rows)} rows)  —  name | value | unit  →  code")
    print("-" * 78)
    resolved = set()
    for rd in rows:
        parsed = ex._parse_biomarker_row(rd)
        if not parsed:
            continue
        name = str(parsed.get("name", ""))
        code = _resolve_biomarker_code(name)
        if code:
            resolved.add(code)
        print(f"  {name[:34]:34} | {str(parsed.get('value','')):>9} | {str(parsed.get('unit',''))[:7]:7} "
              f"->  {code or 'UNMAPPED'}")

    required = {"HGB", "MCV", "RDW", "WBC", "PLT"}
    print("-" * 78)
    print("resolved codes  :", sorted(resolved) or "(none)")
    print("required missing:", sorted(required - resolved) or "none")
    print("\nIf a biomarker is detected in RAW OCR but absent from CLUSTERED ROWS, it's a "
          "row-pairing issue; if it's absent from RAW OCR too, it's an OCR/contrast issue.")
    return 0


def _write_outputs(out_dir: str, result, bundle) -> None:
    """Write the analysis JSON, the two reports, and the deterministic exports."""
    import json as _json

    os.makedirs(out_dir, exist_ok=True)

    def _w(name: str, text: str) -> None:
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    _w("analysis.json", result.to_json())
    if bundle is not None:
        _w("patient_report.txt", bundle.patient_report.report_text)
        _w("clinician_report.txt", bundle.clinician_report.report_text)
        _w("export.json", bundle.exports.json_string)
        _w("export.hl7", bundle.exports.hl7_v2)
        _w("export.csv", bundle.exports.csv)
        _w("export_pdf_metadata.json", _json.dumps(bundle.exports.pdf_metadata, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyse a CBC report (PDF/PNG/JPG) end-to-end.")
    parser.add_argument("file", help="Path to the report (.pdf/.png/.jpg).")
    parser.add_argument("--patient", default="patient_001", help="Patient id (default: patient_001).")
    parser.add_argument("--gender", choices=["M", "F"], default=None, help="Patient gender.")
    parser.add_argument("--age", type=int, default=None, help="Patient age in years.")
    parser.add_argument("--json", default=None, help="Optional path to write the full analysis JSON.")
    parser.add_argument("--out-dir", default=None, help="Directory to write reports + exports (JSON/HL7/CSV/PDF-meta).")
    parser.add_argument("--no-reports", dest="reports", action="store_false",
                        help="Skip Layer 6 LLM report generation (still runs L1–L5 + exports).")
    parser.add_argument("--model", default=None, help="Gemini model id (default: gemini-2.5-flash).")
    parser.add_argument("--show-ocr", action="store_true",
                        help="Diagnostic: print OCR-extracted rows + name→code resolution, then exit.")
    parser.set_defaults(reports=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(analyze(args)))


if __name__ == "__main__":
    main()
