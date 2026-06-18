"""
FastAPI entry point for the Enervara core analysis pipeline.
POST /analyze — upload a PDF, get structured analysis JSON back.
"""

import os
import sys
import tempfile
from typing import Optional

from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

app = FastAPI(
    title="Enervara Medical Report Analysis API",
    version="1.0.0",
    description="Core layer: PDF → structured LFT / CBC analysis",
)


def _get_config(report_type: str):
    rt = report_type.upper()
    if rt == "LFT":
        from configs import lft_config
        return lft_config
    elif rt == "CBC":
        from configs import cbc_config
        return cbc_config
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported report type: {report_type}")


@app.get("/health")
def health():
    return {"status": "ok", "service": "enervara-core"}


@app.post("/extract")
async def extract(
    file: UploadFile = File(..., description="PDF or image of the report"),
    confidence_threshold: float = Query(
        default=0.8, ge=0.0, le=1.0, description="Drop OCR rows below this confidence."
    ),
):
    """
    Extract biomarker rows from a report (the new OCR-first pipeline).

    This is the endpoint the backend microservice calls. It runs
    ``PaddleOCRExtractor`` (PaddleOCR for scanned, pdfplumber for digital) and
    returns the raw rows — name resolution to canonical codes happens in the
    backend (Layer 1→2 adapter), so this service stays panel-agnostic.

    Returns
    -------
    {"rows": [{"name", "value", "unit", "confidence"}, ...], "count": int}
    """
    suffix = os.path.splitext(file.filename or "")[1].lower() or ".pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        from paddle_ocr_extractor import PaddleOCRExtractor

        extractor = PaddleOCRExtractor(confidence_threshold=confidence_threshold)
        rows = await extractor.extract_biomarkers_from_file(tmp_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"OCR error: {exc}")
    finally:
        os.unlink(tmp_path)

    return {"rows": rows, "count": len(rows)}


@app.post("/analyze")
async def analyze(
    file: UploadFile = File(..., description="PDF of the medical report"),
    report_type: str = Query(default="LFT", description="Report type: LFT, CBC"),
):
    """
    Analyze a medical report PDF.

    Extraction is OCR-first (pdfplumber for digital, PaddleOCR for scanned).
    Returns structured JSON with:
    - Parsed markers with flags
    - Condition probabilities (clinical rules)
    - Severity grading
    - Templated medical summary
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    config = _get_config(report_type)

    # Save uploaded file to temp location
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        from core.pipeline import run
        result = run(tmp_path, config=config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline error: {str(e)}")
    finally:
        os.unlink(tmp_path)

    if result.get("errors"):
        return JSONResponse(
            status_code=422,
            content={"errors": result["errors"], "partial_result": result},
        )

    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
