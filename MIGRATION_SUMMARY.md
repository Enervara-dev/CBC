# Migration Summary — PaddleOCR → OCR.space, Local PostgreSQL → Supabase

Infrastructure-only migration. **Layers 2–6 (Normalization, Feature Generation,
Graph Reasoning, Confidence Validation, LLM Presentation) are unchanged** — their
public interfaces and the OCR row schema (`{name, value, unit, confidence}`) are
identical. Only Layer 1 (extraction) and the database transport changed.

New architecture:

```
User → FastAPI backend → OCR.space API (L1) → Supabase PostgreSQL (L2)
                       → Neo4j Aura (L4) → Gemini API (L6)
```

---

## New files

| File | Purpose |
| --- | --- |
| `backend/src/orchestration/ocr_space_client.py` | **OCRSpaceClient** — Layer 1. Calls the OCR.space REST API, validates the JSON, and parses the text into the existing `{name, value, unit, confidence}` row schema. Exposes `extract_biomarkers_from_file(file_path)` (same interface as the old extractors). Includes timeout, bounded retries with exponential backoff, fail-fast on 4xx, graceful `OCRSpaceError`, and structured logging. |
| `backend/tests/test_ocr_space_client.py` | 11 tests: text→row parser, payload/error handling, full extract path against a mocked OCR.space transport, and adapter-compatibility of the parsed rows. |
| `README.md` | Project README with configuration + Render deployment instructions for the new stack. |
| `MIGRATION_SUMMARY.md` | This document. |

## Removed

| Removed | Reason |
| --- | --- |
| `ocr/` (entire directory) | Local PaddleOCR engine, the OCR microservice (`ocr/api`), the legacy standalone OCR app (`core/stage3–7`, `pipeline.py`), configs, and `ocr/requirements.txt` (paddleocr, paddlepaddle, opencv, pdfplumber, pymupdf). |
| `backend/src/orchestration/remote_ocr_client.py` | `RemoteOCRExtractor` (HTTP client to the OCR microservice) — there is no microservice anymore; the backend calls OCR.space directly. |

## Modified

| File | Change |
| --- | --- |
| `backend/src/api/routes.py` | `/api/analyze-file` now uses `OCRSpaceClient` (was `RemoteOCRExtractor`); `OCRSpaceError` → HTTP 502. Docstring updated. **No change** to request/response schema. |
| `analyze_report.py` (CLI) | Layer 1 now `OCRSpaceClient`; removed the `ocr/` path insert; `--show-ocr` rewritten for OCR.space (text rows instead of bbox detections). |
| `backend/src/db/session.py` | Derives the **async (asyncpg)** and **sync (psycopg2)** URLs from a single `DATABASE_URL`. Strips libpq-only params (`sslmode`, `channel_binding`) for asyncpg and sets `ssl=True` (Supabase always TLS); sets `statement_cache_size=0` for the Supabase transaction pooler (`:6543`). `ALEMBIC_DATABASE_URL` is an optional sync override. |
| `backend/src/db/config.py` | `ALEMBIC_DATABASE_URL` documented as optional; `app_env` now reads `ENVIRONMENT` (alias, with legacy `APP_ENV` fallback). |
| `backend/alembic/env.py` | Uses `db.session.sync_database_url()` (derives psycopg2 URL from `DATABASE_URL`). No hard-coded URL. |
| `backend/src/services/graph_reasoning/neo4j_connection.py` | Reads `NEO4J_USERNAME` (documented name) with fallback to `NEO4J_USER`. |
| `.env.example` | Rewritten: `DATABASE_URL` (Supabase), `OCR_SPACE_API_KEY`, `OCR_SPACE_ENDPOINT`, `NEO4J_URI/USERNAME/PASSWORD`, `GEMINI_API_KEY`, `ENVIRONMENT`, `LOG_LEVEL`. No more `localhost` or `OCR_SERVICE_URL`. |
| `backend/requirements.txt` | Single-service deps; OCR is the OCR.space API over `httpx` (no paddle/torch — backend never had them). |
| `requirements.txt` (root) | Removed local-OCR deps (paddleocr, paddlepaddle, opencv, pdfplumber, pymupdf, pillow, numpy); now mirrors the backend set + `httpx`. |
| `render.yaml` | Single `enervera-backend` service (dropped `enervera-ocr`). New env vars: `OCR_SPACE_API_KEY`, `OCR_SPACE_ENDPOINT`, `NEO4J_USERNAME`, `ENVIRONMENT`. Removed `OCR_SERVICE_URL`, `ALEMBIC_DATABASE_URL`. |
| `ARCHITECTURE.md` | Layer 1, data flow, tech stack, orchestration/API sections, status table, environment notes, and §8 deployment updated for OCR.space + Supabase + single service. |

## Environment variables (final)

```
DATABASE_URL=postgresql://postgres:<pwd>@db.<ref>.supabase.co:5432/postgres?sslmode=require
OCR_SPACE_API_KEY=<key>
OCR_SPACE_ENDPOINT=https://api.ocr.space/parse/image
NEO4J_URI=<aura-uri>
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=<pwd>
GEMINI_API_KEY=<key>
ENVIRONMENT=production
LOG_LEVEL=INFO
```

## Compatibility / validation

- **Layers 2–6 untouched** — verified by the existing suites (L2 37 · L3 26 · L4 15 · L5 8 · L6 16, all still passing).
- **OCR row schema preserved** — `test_ocr_space_client.py` asserts parsed rows feed `Layer1ToLayer2Adapter` and resolve the required CBC codes.
- **No `localhost` DB assumptions** — URLs come only from `DATABASE_URL`; drivers/SSL derived in `db.session` (unit-verified for direct + pooler Supabase URLs).
- **Alembic + seeds** — `alembic/env.py` and `db.seed_runner` use the derived sync URL; run `alembic upgrade head` then `python -m db.seed_runner` against Supabase.
- **Full suite:** `127 passed`.

## One-time operator steps (Supabase)

```bash
cd backend
alembic upgrade head
cd src && python -m db.seed_runner
```

## Notes / follow-ups

- OCR.space (text mode) does not return per-token confidence, so parsed rows carry a fixed nominal confidence (`DEFAULT_ROW_CONFIDENCE = 0.9`); the adapter uses confidence only for audit metadata, not filtering.
- Live end-to-end validation (real OCR.space key + real Supabase/Neo4j/Gemini) was **not** run in this dev environment; the OCR path is covered by mocked-transport tests. Run one real PDF and one image through `/api/analyze-file` (or `analyze_report.py`) after deploying.
