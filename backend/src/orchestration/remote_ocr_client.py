"""
Remote OCR client — calls the OCR microservice over HTTP.

In the two-service Render deployment, OCR (PaddleOCR, heavy) runs as a separate
service. This client posts a report file to that service's ``POST /extract`` and
returns the biomarker rows, so the backend never imports paddle.

``RemoteOCRExtractor`` matches the ``extract_biomarkers_from_file`` interface used
by the rest of the pipeline, so it is a drop-in for the local ``PaddleOCRExtractor``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

DEFAULT_OCR_TIMEOUT = 120.0


class RemoteOCRError(RuntimeError):
    """The OCR microservice was unreachable or returned an error."""


class RemoteOCRExtractor:
    """OCR extractor that delegates to the OCR microservice via HTTP."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = DEFAULT_OCR_TIMEOUT,
    ) -> None:
        url = (base_url or os.getenv("OCR_SERVICE_URL", "")).strip().rstrip("/")
        if not url:
            raise RemoteOCRError(
                "OCR_SERVICE_URL is not set; cannot reach the OCR microservice."
            )
        # Tolerate a scheme-less value (e.g. host:port) — default to http.
        if "://" not in url:
            url = "http://" + url
        self.base_url = url
        self.timeout = timeout

    async def extract_biomarkers_from_file(self, file_path: str) -> List[Dict[str, Any]]:
        """POST the file to ``/extract`` and return ``[{name,value,unit,confidence}]``."""
        import httpx  # local import keeps httpx optional for non-file callers

        url = f"{self.base_url}/extract"
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as fh:
            files = {"file": (filename, fh, "application/octet-stream")}
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, files=files)
            except httpx.HTTPError as exc:
                raise RemoteOCRError(f"OCR service request failed ({url}): {exc}") from exc

        if resp.status_code >= 400:
            raise RemoteOCRError(
                f"OCR service returned {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()
        rows = data.get("rows", [])
        logger.info("Remote OCR returned %d rows from %s", len(rows), filename)
        return rows
