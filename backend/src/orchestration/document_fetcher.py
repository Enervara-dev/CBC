"""
Secure downloader for presigned document URLs (/api/analyze-url).

The URL we are handed is a short-lived credential (an S3 presigned URL carries
its signature in the query string), so:

  * only http/https schemes are accepted — no file://, no gopher;
  * redirects are NOT followed (the signature covers exactly one object, and a
    redirect would take us somewhere the caller never named);
  * the download is streamed under a byte cap so a misbehaving host cannot
    fill the disk;
  * the on-disk type is decided by magic-number sniffing, not by the URL path
    or the caller's Content-Type claim;
  * logs record host + path only — the query string is a secret and is never
    written anywhere.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Optional, Tuple
from urllib.parse import urlparse

import httpx

DEFAULT_MAX_BYTES = 20 * 1024 * 1024  # 20 MB — well above any lab report page
DEFAULT_TIMEOUT_S = 30.0

_MAGIC = (
    (b"%PDF-", ".pdf"),
    (b"\x89PNG", ".png"),
    (b"\xff\xd8\xff", ".jpg"),  # JPEG SOI + first byte of APP0/APP1
)
_SNIFF_LEN = max(len(m) for m, _ in _MAGIC)


class DocumentFetchError(Exception):
    """The document could not be downloaded (network, status, scheme...)."""
    code = "download_failed"


class DocumentTooLargeError(DocumentFetchError):
    """The download exceeded the byte cap."""
    code = "too_large"


class UnsupportedDocumentError(DocumentFetchError):
    """Scheme not allowed, or bytes are not a PDF/PNG/JPEG at all."""
    code = "unsupported_type"


def sniff_extension(path: str) -> Optional[str]:
    """Return the canonical extension implied by the file's magic bytes."""
    with open(path, "rb") as fh:
        head = fh.read(_SNIFF_LEN)
    for magic, ext in _MAGIC:
        if head.startswith(magic):
            return ext
    return None


def _log_target(url: str) -> str:
    """host + path, never the query string."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def fetch_document_to_temp_file(
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    transport: Optional[httpx.BaseTransport] = None,
    logger: Optional[logging.Logger] = None,
) -> Tuple[str, str]:
    """
    Download ``url`` to a fresh temp file and return ``(path, extension)``.

    The caller owns the temp file and must delete it. ``transport`` is
    injectable so tests can drive this with ``httpx.MockTransport`` and no
    network.
    """
    log = logger or logging.getLogger(__name__)

    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise UnsupportedDocumentError(
            f"URL scheme must be http(s), got {parsed.scheme or '<none>'!r}"
        )

    target = _log_target(url)
    client_kwargs = {"timeout": timeout_s, "follow_redirects": False}
    if transport is not None:
        client_kwargs["transport"] = transport

    fd, tmp_path = tempfile.mkstemp(prefix="cbc-url-", suffix=".bin")
    written = 0
    try:
        with httpx.Client(**client_kwargs) as client, os.fdopen(fd, "wb") as out:
            with client.stream("GET", url) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    raise DocumentFetchError(
                        f"refusing redirect (status {response.status_code}) from {target}"
                    )
                if response.status_code != 200:
                    raise DocumentFetchError(
                        f"GET {target} returned status {response.status_code}"
                    )
                log.info("Downloading document from %s", target)
                for chunk in response.iter_bytes():
                    written += len(chunk)
                    if written > max_bytes:
                        raise DocumentTooLargeError(
                            f"document from {target} exceeded {max_bytes} bytes"
                        )
                    out.write(chunk)
    except DocumentFetchError:
        os.unlink(tmp_path)
        raise
    except httpx.HTTPError as exc:
        os.unlink(tmp_path)
        raise DocumentFetchError(f"download from {target} failed: {exc}") from exc

    ext = sniff_extension(tmp_path)
    if ext is None:
        os.unlink(tmp_path)
        raise UnsupportedDocumentError(
            "downloaded bytes are not a supported document (PDF, PNG or JPEG)"
        )
    final_path = f"{tmp_path[: -len('.bin')]}{ext}"
    os.replace(tmp_path, final_path)
    log.info("Document fetched: %d bytes (%s) from %s", written, ext, target)
    return final_path, ext
