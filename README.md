# ENERVERA — Blood Report Interpretation System

A layered, knowledge-graph-first pipeline that turns a CBC lab report (PDF/image)
into validated clinical findings, evidence, and patient/clinician reports.

```
User
  → FastAPI backend (single service, all six layers)
      → OCR.space API          (Layer 1 — extraction)
      → Supabase PostgreSQL    (Layer 2 — reference ranges)
      → Neo4j Aura             (Layer 4 — graph reasoning)
      → Gemini API             (Layer 6 — presentation)
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full layer-by-layer design and
[backend/src/domains/README.md](backend/src/domains/README.md) for adding a new
lab panel.

---

## Prerequisites

- Python 3.11
- A Supabase project (PostgreSQL)
- An OCR.space API key (https://ocr.space/ocrapi)
- A Neo4j instance with the CBC knowledge graph loaded — **local** for now
  (`neo4j://localhost:7687`); switch `NEO4J_URI` to Neo4j Aura (`neo4j+s://…`) later
  with no code change.
- A Google Gemini API key

## Configuration

All configuration is via environment variables (no hard-coded credentials). Copy
the template and fill it in:

```bash
cp .env.example .env
```

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Supabase connection string. Async (asyncpg) + sync (psycopg2) drivers are derived from it automatically. Keep `?sslmode=require`. |
| `ALEMBIC_DATABASE_URL` | *Optional* explicit override for the migrations/seeds (sync) URL. |
| `OCR_SPACE_API_KEY` | OCR.space API key (Layer 1). |
| `OCR_SPACE_ENDPOINT` | OCR.space endpoint (default `https://api.ocr.space/parse/image`). |
| `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` | Neo4j Aura (Layer 4). |
| `GEMINI_API_KEY` | Google Gemini (Layer 6). |
| `ENVIRONMENT` / `LOG_LEVEL` | Deployment env + log level. |

> **Supabase connection string** — Project → Settings → Database → *Connection
> string*. The **direct** URL (`db.<ref>.supabase.co:5432`) works as-is. If you use
> the **transaction pooler** (`...pooler.supabase.com:6543`), the app automatically
> disables server-side prepared statements (asyncpg + pgbouncer requirement).

## Install & run (local)

```bash
pip install -r requirements.txt          # or backend/requirements.txt

# 1. Migrate + seed the Supabase database (one-time)
cd backend
alembic upgrade head
cd src && python -m db.seed_runner

# 2. Run the API (from backend/src)
uvicorn api.main:app --reload
```

Analyse a single report from the command line (all six layers):

```bash
python analyze_report.py path/to/report.pdf --gender F --age 35
python analyze_report.py scan.png --show-ocr     # debug: what did OCR read?
```

## API

- `POST /api/analyze` — JSON `{patient_id, biomarker_values, gender, age_years}` → findings + `recommendations`. Pass `"include_reports": true` to also get the Layer 6 `reports` bundle (patient/clinician reports + JSON/HL7/CSV/PDF exports; requires `GEMINI_API_KEY`).
- `POST /api/analyze-file` — multipart PDF/image upload → OCR.space → findings (form field `include_reports=true` for reports).
- `GET /api/health` — liveness + readiness.

## Deployment (Render)

A single web service ([render.yaml](render.yaml)):

| Service | Root | Start command | Health |
| --- | --- | --- | --- |
| `enervera-backend` | `backend/` | `uvicorn src.api.main:app --host 0.0.0.0 --port $PORT` | `/api/health` |

1. Create the service from `render.yaml` (Blueprint) or manually.
2. Set the secrets in the Render dashboard (all `sync:false`): `DATABASE_URL`,
   `OCR_SPACE_API_KEY`, `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`,
   `GEMINI_API_KEY`.
3. Run the one-time DB setup against Supabase: `alembic upgrade head` then
   `python -m db.seed_runner` (from `backend/` / `backend/src`).

There is **no** separate OCR service and **no** local PostgreSQL — OCR is the
OCR.space API and the database is Supabase.

## Tests

```bash
cd backend && python -m pytest -q
```
