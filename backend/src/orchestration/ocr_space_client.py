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
    context the asyncpg (Supabase) engine passes for the DB connection.
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
_NUMBER = re.compile(r"[<>]?\s*(\d+(?:[.,]\d+)?)")
# A unit-looking token (letters, %, slash, caret, digits) immediately after the value.
_UNIT_TOKEN = re.compile(r"[A-Za-z%/][A-Za-z0-9%/^.\-]*")


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
        ocr_engine: int = 2,
        language: str = "eng",
    ) -> None:
        self.api_key = (api_key or os.getenv("OCR_SPACE_API_KEY", "")).strip()
        self.endpoint = (endpoint or os.getenv("OCR_SPACE_ENDPOINT", DEFAULT_ENDPOINT)).strip()
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
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
        """POST the file to OCR.space and return the concatenated parsed text."""
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
                value = float(match.group(1).replace(",", ""))
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
