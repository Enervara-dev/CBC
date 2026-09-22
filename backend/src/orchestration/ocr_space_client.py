"""
OCR.space client — Layer 1 extraction over the OCR.space REST API.

Replaces the local PaddleOCR microservice. The backend sends a PDF/image to
OCR.space, receives the recognised text, and parses it into the biomarker row
schema the rest of the pipeline already expects::

    [{"name": "Hemoglobin", "value": 10.2, "unit": "g/dL", "confidence": 0.9}, ...]

``OCRSpaceClient`` exposes ``extract_biomarkers_from_file(file_path)`` — the same
interface as the old ``PaddleOCRExtractor`` / ``RemoteOCRExtractor`` — so
``Layer1ToLayer2Adapter``, ``CBCOrchestrator`` and the normalization layer are
untouched.

Configuration (environment / project ``.env``)::

    OCR_SPACE_API_KEY   required — your OCR.space API key
    OCR_SPACE_ENDPOINT  optional — defaults to https://api.ocr.space/parse/image

Robustness: per-call timeout, bounded retries with backoff on network / 5xx /
transient OCR errors, explicit API-error handling, and structured logging. Any
unrecoverable problem raises :class:`OCRSpaceError` (mapped to HTTP 502 upstream).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import ssl
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Best-effort: make the project .env available to os.getenv (no override of real env),
# mirroring how the Neo4j connection bootstraps its config.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)
except Exception:  # python-dotenv missing or .env absent — fall back to process env
    pass


def _truststore_verify() -> Union[bool, ssl.SSLContext]:
    """Return an SSL context that verifies against the **OS trust store**.

    Without it, TLS to OCR.space fails behind a proxy that injects its own root CA
    (``CERTIFICATE_VERIFY_FAILED``). We build a *local* ``truststore`` context and
    hand it to httpx's ``verify`` rather than calling ``truststore.inject_into_ssl()``
    — global injection monkeypatches ``ssl.SSLContext`` and breaks the explicit SSL
    context the asyncpg (Aurora) engine passes for the DB connection.
    """
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # truststore missing — fall back to the default CA bundle
        return True


logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://api.ocr.space/parse/image"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 3
# OCR.space (text mode) does not return per-token confidence, so parsed rows get a
# fixed nominal confidence. The Layer-1 adapter uses it only for audit metadata.
DEFAULT_ROW_CONFIDENCE = 0.9

# Extension → OCR.space `filetype` hint (supported: PDF, PNG, JPG, GIF, TIF, BMP).
_FILETYPE_BY_EXT: Dict[str, str] = {
    ".pdf": "PDF", ".png": "PNG", ".jpg": "JPG", ".jpeg": "JPG",
    ".gif": "GIF", ".tif": "TIF", ".tiff": "TIF", ".bmp": "BMP",
}

# First numeric token in a line (the biomarker value). Comma treated as a thousands
# separator (stripped) to match the previous PaddleOCR parser's behaviour.
# A numeric token, allowing grouped digits: "85.6", "0,4", "150,000", "2,88,000".
# Whether a comma is a decimal point or a group separator is decided in
# :func:`_parse_number` — the two are indistinguishable to the regex.
_NUMBER = re.compile(r"[<>]?\s*(\d+(?:[.,]\d+)*)")
# A unit-looking token (letters, %, slash, caret, digits) immediately after the value.
_UNIT_TOKEN = re.compile(r"[A-Za-z%/][A-Za-z0-9%/^.\-]*")


# Largest upload we will send to OCR.space in one request. The free plan rejects
# anything over 1.5 MB (E556); this sits below it to leave room for the multipart
# envelope. Oversized PDFs are split per page instead of failing.
DEFAULT_MAX_UPLOAD_BYTES = 1_200_000


def _needs_page_split(file_path: str, max_bytes: int) -> bool:
    """True if ``file_path`` is a PDF too large to upload in one request."""
    if os.path.splitext(file_path)[1].lower() != ".pdf":
        return False          # images cannot be split; send them and let OCR decide
    try:
        return os.path.getsize(file_path) > max_bytes
    except OSError:
        return False


def _split_pdf_pages(file_path: str) -> List[str]:
    """
    Write each page of ``file_path`` to its own temp PDF and return the paths.

    The caller owns the returned files and must delete them.
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as exc:   # pragma: no cover - dependency is declared
        raise OCRSpaceError(
            "pypdf is required to split oversized PDFs for OCR. Run: pip install pypdf"
        ) from exc

    try:
        reader = PdfReader(file_path)
    except Exception as exc:
        raise OCRSpaceError(f"Could not read PDF {os.path.basename(file_path)}: {exc}") from exc

    paths: List[str] = []
    for index, page in enumerate(reader.pages, start=1):
        writer = PdfWriter()
        writer.add_page(page)
        handle = tempfile.NamedTemporaryFile(
            delete=False, suffix=f"_p{index:03d}.pdf", prefix="ocr_page_"
        )
        try:
            writer.write(handle)
        finally:
            handle.close()
        paths.append(handle.name)
    return paths


def _parse_number(token: str) -> float:
    """
    Convert a numeric token to a float, resolving comma ambiguity.

    A comma in a lab report is either a decimal point ("0,4" = 0.4 — common on
    Indian and European printers) or a digit-group separator ("150,000", and the
    Indian lakh grouping "2,88,000"). Stripping every comma unconditionally — the
    previous behaviour — turned a total bilirubin of 0,4 mg/dL into 4.0, a 10x
    error that read as jaundice.

    The rule: a *single* comma trailed by one or two digits is a decimal point;
    anything else (several commas, a trailing group of three, or a token that also
    contains a period) is digit grouping.
    """
    token = token.strip()
    if "," not in token:
        return float(token)
    if "." in token:                      # "1,234.5" — comma must be grouping
        return float(token.replace(",", ""))
    parts = token.split(",")
    if len(parts) == 2 and len(parts[1]) in (1, 2):
        return float(f"{parts[0]}.{parts[1]}")
    return float(token.replace(",", ""))


class OCRSpaceError(RuntimeError):
    """OCR.space was unreachable, rejected the request, or returned no usable text."""


class OCRSpaceClient:
    """Extract biomarker rows from a report file via the OCR.space API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
        ocr_engine: int = 2,
        language: str = "eng",
    ) -> None:
        self.api_key = (api_key or os.getenv("OCR_SPACE_API_KEY", "")).strip()
        self.endpoint = (endpoint or os.getenv("OCR_SPACE_ENDPOINT", DEFAULT_ENDPOINT)).strip()
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.max_upload_bytes = max_upload_bytes
        self.ocr_engine = ocr_engine
        self.language = language
        if not self.api_key:
            raise OCRSpaceError(
                "OCR_SPACE_API_KEY is not set; cannot call the OCR.space API."
            )
        self._verify = _truststore_verify()

    # ── Public API (unchanged interface for the rest of the pipeline) ──────────
    async def extract_biomarkers_from_file(self, file_path: str) -> List[Dict[str, Any]]:
        """
        OCR ``file_path`` via OCR.space and return biomarker rows.

        Returns
        -------
        List[Dict[str, Any]]
            ``[{"name", "value", "unit", "confidence"}, ...]``.

        Raises
        ------
        FileNotFoundError
            If the file does not exist.
        OCRSpaceError
            On API/transport failure, or if no biomarker rows can be parsed.
        """
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"OCR input file not found: {file_path}")

        text = await self._ocr_text(file_path)
        rows = self._parse_text(text)
        if not rows:
            logger.error("OCR.space returned text but no biomarker rows parsed (%s).", file_path)
            raise OCRSpaceError("No biomarkers found in OCR output")
        logger.info("OCR.space extracted %d biomarker rows from %s", len(rows), os.path.basename(file_path))
        return rows

    # ── OCR.space call (with retries) ──────────────────────────────────────────
    async def _ocr_text(self, file_path: str) -> str:
        """
        OCR ``file_path``, splitting oversized PDFs into single pages first.

        OCR.space enforces a per-upload size cap (1.5 MB on the free plan) and
        rejects anything larger with HTTP 413 / E556 — which this client turns
        into an ``OCRSpaceError`` and the API surfaces as a 502. Real lab reports
        are multi-page scans that routinely exceed it: the three used to validate
        this pipeline were 3.9 MB, 2.6 MB and 2.2 MB, so *every one of them*
        failed outright while their individual pages were 149-490 KB.

        So a PDF over the threshold is split and OCR'd a page at a time, and the
        page texts are concatenated in order — the same text a single call would
        have produced. A page that fails is recorded inline and the rest continue,
        so one bad page no longer costs the whole report.
        """
        if _needs_page_split(file_path, self.max_upload_bytes):
            return await self._ocr_pdf_by_page(file_path)
        return await self._ocr_single(file_path)

    async def _ocr_pdf_by_page(self, file_path: str) -> str:
        """Split a PDF into single-page files and OCR each, preserving order."""
        pages = _split_pdf_pages(file_path)
        logger.info("OCR: %s exceeds %d bytes — split into %d pages",
                    os.path.basename(file_path), self.max_upload_bytes, len(pages))
        chunks: List[str] = []
        failures = 0
        try:
            for index, page_path in enumerate(pages, start=1):
                try:
                    chunks.append(await self._ocr_single(page_path))
                except OCRSpaceError as exc:
                    failures += 1
                    logger.warning("OCR failed on page %d/%d: %s", index, len(pages), exc)
                    chunks.append(f"<<OCR failed on page {index}: {exc}>>")
        finally:
            for page_path in pages:
                try:
                    os.unlink(page_path)
                except OSError:
                    pass

        if failures == len(pages):
            raise OCRSpaceError(
                f"OCR failed on all {len(pages)} pages of {os.path.basename(file_path)}."
            )
        text = "\n".join(chunks).strip()
        if not text:
            raise OCRSpaceError(f"No text recovered from {os.path.basename(file_path)}.")
        logger.info("OCR: %d/%d pages succeeded, %d chars total",
                    len(pages) - failures, len(pages), len(text))
        return text

    async def _ocr_single(self, file_path: str) -> str:
        """POST one file to OCR.space and return the concatenated parsed text."""
        import httpx  # local import keeps httpx optional for non-file callers

        ext = os.path.splitext(file_path)[1].lower()
        filetype = _FILETYPE_BY_EXT.get(ext)
        data = {
            "apikey": self.api_key,
            "language": self.language,
            "OCREngine": str(self.ocr_engine),
            "isTable": "true",            # lab reports are tabular → better row grouping
            "scale": "true",
            "isOverlayRequired": "false",
        }
        if filetype:
            data["filetype"] = filetype

        filename = os.path.basename(file_path)
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                with open(file_path, "rb") as fh:
                    files = {"file": (filename, fh, "application/octet-stream")}
                    async with httpx.AsyncClient(timeout=self.timeout, verify=self._verify) as client:
                        resp = await client.post(self.endpoint, data=data, files=files)
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning("OCR.space request error (attempt %d/%d): %s",
                               attempt, self.max_retries, exc)
                await self._backoff(attempt)
                continue

            # Retry on transient server-side errors; fail fast on client errors.
            if resp.status_code >= 500:
                last_error = OCRSpaceError(f"OCR.space {resp.status_code}: {resp.text[:200]}")
                logger.warning("OCR.space server error (attempt %d/%d): %s",
                               attempt, self.max_retries, last_error)
                await self._backoff(attempt)
                continue
            if resp.status_code >= 400:
                raise OCRSpaceError(f"OCR.space rejected the request ({resp.status_code}): {resp.text[:200]}")

            try:
                payload = resp.json()
            except ValueError as exc:
                raise OCRSpaceError(f"OCR.space returned non-JSON response: {resp.text[:200]}") from exc

            return self._text_from_payload(payload)

        raise OCRSpaceError(f"OCR.space unavailable after {self.max_retries} attempts: {last_error}")

    async def _backoff(self, attempt: int) -> None:
        """Exponential backoff between retries (0.5s, 1s, 2s, …)."""
        await asyncio.sleep(0.5 * (2 ** (attempt - 1)))

    @staticmethod
    def _text_from_payload(payload: Dict[str, Any]) -> str:
        """Validate an OCR.space JSON payload and return its concatenated text."""
        if payload.get("IsErroredOnProcessing"):
            msg = payload.get("ErrorMessage") or payload.get("ErrorDetails") or "unknown error"
            if isinstance(msg, list):
                msg = "; ".join(str(m) for m in msg)
            raise OCRSpaceError(f"OCR.space processing error: {msg}")

        # OCRExitCode: 1=success, 2=partial success (still has text), others=failure.
        exit_code = payload.get("OCRExitCode")
        results = payload.get("ParsedResults") or []
        text = "\n".join((r.get("ParsedText") or "") for r in results).strip()
        if not text:
            raise OCRSpaceError(f"OCR.space returned no text (OCRExitCode={exit_code}).")
        return text

    # ── Text → biomarker rows ──────────────────────────────────────────────────
    def _parse_text(self, text: str) -> List[Dict[str, Any]]:
        """
        Parse OCR'd report text into ``[{name, value, unit, confidence}]`` rows.

        Per line: the text before the first numeric token is the biomarker name,
        the first numeric token is the value, and the token right after it (if it
        looks like a unit) is the unit. Lines with no leading name or no numeric
        value are skipped. Canonical name resolution happens later in the adapter.
        """
        rows: List[Dict[str, Any]] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = _NUMBER.search(line)
            if not match:
                continue
            name = line[: match.start()].strip(" :|\t-")
            # Need a real label (at least one letter) preceding the value.
            if not name or not re.search(r"[A-Za-z]", name):
                continue
            try:
                value = _parse_number(match.group(1))
            except ValueError:
                continue

            unit = ""
            rest = line[match.end():].strip()
            if rest:
                unit_match = _UNIT_TOKEN.match(rest)
                if unit_match:
                    unit = unit_match.group()

            rows.append({
                "name": name,
                "value": value,
                "unit": unit,
                "confidence": DEFAULT_ROW_CONFIDENCE,
            })
        return rows
