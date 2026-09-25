"""
Layer-1 provider selection.

Two extractors implement the same contract —
``extract_biomarkers_from_file(path) -> [{name, value, unit, confidence, ...}]``:

``textract``  AWS Textract table analysis. Reads label / result / unit / reference
              from their own table columns, so wrapped labels and drifting
              columns resolve correctly. Billed per page; HIPAA-eligible under a
              BAA. Requires ``boto3``, AWS credentials, and a region that offers
              Textract (``eu-north-1`` does not).
``ocrspace``  OCR.space flat text. Free tier, but the parser must infer that a
              label and a value belong together from sharing a line, which real
              lab reports break.

Select with ``OCR_PROVIDER``; ``ocrspace`` remains the default so existing
deployments are unaffected until they opt in.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "ocrspace"
_PROVIDERS = ("ocrspace", "textract")


def configured_provider() -> str:
    """The provider name from ``OCR_PROVIDER`` (lowercased), or the default."""
    return (os.getenv("OCR_PROVIDER") or DEFAULT_PROVIDER).strip().lower()


def get_ocr_client(provider: str | None = None) -> Any:
    """
    Build the configured Layer-1 extractor.

    Raises
    ------
    ValueError
        If ``provider`` is not one of :data:`_PROVIDERS`. Failing loudly beats
        silently falling back — a typo in the env var would otherwise send
        patient reports to the provider the operator was trying to move off.
    """
    name = (provider or configured_provider()).strip().lower()
    if name not in _PROVIDERS:
        raise ValueError(
            f"Unknown OCR_PROVIDER {name!r}. Available: {', '.join(_PROVIDERS)}."
        )

    if name == "textract":
        from orchestration.textract_client import TextractClient

        client = TextractClient()
        logger.info("Layer 1: AWS Textract (region=%s, table analysis)", client.region_name)
        return client

    from orchestration.ocr_space_client import OCRSpaceClient

    logger.info("Layer 1: OCR.space (flat text)")
    return OCRSpaceClient()


__all__ = ["get_ocr_client", "configured_provider", "DEFAULT_PROVIDER"]
