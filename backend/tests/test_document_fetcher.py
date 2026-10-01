"""
Unit tests for the presigned-URL downloader (orchestration/document_fetcher.py).

All HTTP is faked with httpx.MockTransport — no network, no real URLs. The
security properties under test: scheme allow-list, redirect refusal, byte cap,
magic-number type sniffing, and (critically) that the URL's query string — the
presigned signature — never appears in logs or error messages.
"""

import os

import httpx
import pytest

from orchestration.document_fetcher import (
    DocumentFetchError,
    DocumentTooLargeError,
    UnsupportedDocumentError,
    fetch_document_to_temp_file,
    sniff_extension,
)

PDF_BYTES = b"%PDF-1.4\r%fake lab report\r>>"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
SIGNED_URL = "https://bucket.s3.amazonaws.com/report.pdf?X-Amz-Signature=topsecret"


def _fetch(url, handler, **kwargs):
    transport = httpx.MockTransport(handler)
    return fetch_document_to_temp_file(url, transport=transport, **kwargs)


def _cleanup(path):
    if os.path.exists(path):
        os.unlink(path)


class TestHappyPaths:
    def test_downloads_and_sniffs_pdf(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            # The full signed URL goes to its own host (that is how presigned
            # URLs work); the fetcher's job is only to keep it out of logs.
            assert request.url.host == "bucket.s3.amazonaws.com"
            assert request.url.path == "/report.pdf"
            return httpx.Response(200, content=PDF_BYTES)

        path, ext = _fetch(SIGNED_URL, handler)
        try:
            assert ext == ".pdf"
            assert path.endswith(".pdf")
            with open(path, "rb") as fh:
                assert fh.read() == PDF_BYTES
        finally:
            _cleanup(path)

    def test_sniffs_png(self):
        path, ext = _fetch(SIGNED_URL, lambda r: httpx.Response(200, content=PNG_BYTES))
        try:
            assert ext == ".png"
        finally:
            _cleanup(path)

    def test_sniffs_jpeg(self):
        path, ext = _fetch(SIGNED_URL, lambda r: httpx.Response(200, content=JPG_BYTES))
        try:
            assert ext == ".jpg"
        finally:
            _cleanup(path)

    def test_accepts_http(self):
        path, ext = _fetch("http://internal.host/report.pdf",
                           lambda r: httpx.Response(200, content=PDF_BYTES))
        try:
            assert ext == ".pdf"
        finally:
            _cleanup(path)


class TestRejections:
    @pytest.mark.parametrize("url", [
        "ftp://example.com/report.pdf",
        "file:///C:/Windows/System32/drivers/etc/hosts",
        "not-a-url",
        "//bucket.s3.amazonaws.com/report.pdf",
    ])
    def test_rejects_non_http_schemes(self, url):
        with pytest.raises(UnsupportedDocumentError):
            _fetch(url, lambda r: httpx.Response(200, content=PDF_BYTES))

    def test_rejects_non_document_bytes(self):
        with pytest.raises(UnsupportedDocumentError):
            _fetch(SIGNED_URL, lambda r: httpx.Response(200, content=b"<!DOCTYPE html>"))

    def test_rejects_http_error_status(self):
        with pytest.raises(DocumentFetchError):
            _fetch(SIGNED_URL, lambda r: httpx.Response(404, content=b"nope"))

    def test_rejects_redirects(self):
        # A presigned signature covers exactly one object; following a redirect
        # would take us somewhere the caller never named.
        with pytest.raises(DocumentFetchError, match="redirect"):
            _fetch(SIGNED_URL, lambda r: httpx.Response(302, headers={"Location": "https://evil.example.com/x"}))

    def test_enforces_byte_cap(self):
        big = PDF_BYTES + b"\x00" * (64 * 1024)
        with pytest.raises(DocumentTooLargeError):
            _fetch(SIGNED_URL, lambda r: httpx.Response(200, content=big), max_bytes=1024)

    def test_wraps_network_errors(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(DocumentFetchError, match="connection refused"):
            _fetch(SIGNED_URL, handler)

    def test_query_string_never_appears_in_errors(self):
        # The presigned signature is a credential; it must not leak into
        # exception messages (which end up in logs).
        try:
            _fetch(SIGNED_URL, lambda r: httpx.Response(403, content=b"denied"))
            pytest.fail("expected DocumentFetchError")
        except DocumentFetchError as exc:
            assert "topsecret" not in str(exc)

        try:
            _fetch(SIGNED_URL, lambda r: httpx.Response(200, content=b"not a doc"))
            pytest.fail("expected UnsupportedDocumentError")
        except UnsupportedDocumentError as exc:
            assert "topsecret" not in str(exc)

        try:
            _fetch(SIGNED_URL, lambda r: httpx.Response(200, content=b"\x00" * 64),
                   max_bytes=16)
            pytest.fail("expected DocumentTooLargeError")
        except DocumentTooLargeError as exc:
            assert "topsecret" not in str(exc)

    def test_failed_download_leaves_no_temp_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TMPDIR", str(tmp_path))
        monkeypatch.setenv("TEMP", str(tmp_path))
        monkeypatch.setenv("TMP", str(tmp_path))
        before = set(os.listdir(tmp_path))
        with pytest.raises(DocumentFetchError):
            _fetch(SIGNED_URL, lambda r: httpx.Response(500))
        assert set(os.listdir(tmp_path)) == before


class TestSniffExtension:
    def test_sniff_extension_reads_magic(self, tmp_path):
        p = tmp_path / "doc.bin"
        p.write_bytes(PNG_BYTES)
        assert sniff_extension(str(p)) == ".png"

    def test_sniff_extension_unknown(self, tmp_path):
        p = tmp_path / "doc.bin"
        p.write_bytes(b"PK\x03\x04 zip")
        assert sniff_extension(str(p)) is None
