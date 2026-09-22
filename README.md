# ENERVERA — Blood Report Interpretation System

A layered, knowledge-graph-first pipeline that turns a lab report (PDF/image)
into validated clinical findings, evidence, and patient/clinician reports.
Panels supported today: **CBC** (Complete Blood Count), **LFT** (Liver Function
Test), and **Lipid Profile** — the Neo4j graph (Layer 4) currently covers CBC.

```
User
  → FastAPI backend (single service, all six layers)
      → OCR.space API          (Layer 1 — extraction)
      → AWS Aurora PostgreSQL  (Layer 2 — reference ranges)
      → Neo4j Aura             (Layer 4 — graph reasoning)
      → Gemini API             (Layer 6 — presentation)
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full layer-by-layer design and
[backend/src/domains/README.md](backend/src/domains/README.md) for adding a new
lab panel.

---

## Prerequisites

- Python 3.11
- PostgreSQL: the `cbc` database on the AWS Aurora cluster `enervara-aurora-pg17`
  (`ap-south-1`) — or any local PostgreSQL 15+ for development
- An OCR.space API key (https://ocr.space/ocrapi)
- A Neo4j instance with the CBC knowledge graph loaded (LFT/Lipid subgraphs are
  not built yet) — **local** for now
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
| `DATABASE_URL` | Aurora PostgreSQL connection string. Async (asyncpg) + sync (psycopg2) drivers are derived from it automatically; TLS is required automatically for `*.rds.amazonaws.com` hosts. |
| `ALEMBIC_DATABASE_URL` | *Optional* explicit override for the migrations/seeds (sync) URL. |
| `OCR_PROVIDER` | Layer-1 extractor: `ocrspace` (default, flat text, free) or `textract` (AWS table analysis — reads label/value/unit/reference from their own columns, HIPAA-eligible, billed per page). |
| `AWS_REGION` | Region for Textract, e.g. `ap-south-1`. **Textract is not offered in every region** — `eu-north-1` has no endpoint. |
| `OCR_SPACE_API_KEY` | OCR.space API key (Layer 1, when `OCR_PROVIDER=ocrspace`). |
| `OCR_SPACE_ENDPOINT` | OCR.space endpoint (default `https://api.ocr.space/parse/image`). |
| `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` | Neo4j Aura (Layer 4). |
| `GEMINI_API_KEY` | Google Gemini (Layer 6). |
| `ENVIRONMENT` / `LOG_LEVEL` | Deployment env + log level. |

> **Aurora connection string** —
> `postgresql://cbc_app:<password>@enervara-aurora-pg17.cluster-<id>.ap-south-1.rds.amazonaws.com:5432/cbc`.
> Use the **cluster (writer) endpoint** and the least-privilege `cbc_app` role, never
> the cluster master user. TLS is enforced automatically for RDS hosts; an explicit
> `?sslmode=` in the URL overrides it. Only if you put a transaction-pooling proxy
> such as pgbouncer in front, append `?statement_cache_size=0`.

## Install & run (local)

```bash
pip install -r requirements.txt          # or backend/requirements.txt

# 1. Migrate + seed the Aurora `cbc` database (one-time; seeds every registered
#    panel's reference ranges — CBC, LFT, Lipid — and is safe to re-run)
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

- `POST /api/analyze` — JSON `{patient_id, biomarker_values, gender, age_years}` → findings + `recommendations`. `biomarker_values` may be any registered panel (e.g. `{"ALT": 180, "AST": 95, "ALP": 210, "TBIL": 3.4, "ALB": 2.8}`); the detected panel's required markers are what is enforced. Pass `"include_reports": true` to also get the Layer 6 `reports` bundle (patient/clinician reports + JSON/HL7/CSV/PDF exports; requires `GEMINI_API_KEY`).
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
3. Run the one-time DB setup against Aurora: `alembic upgrade head` then
   `python -m db.seed_runner` (from `backend/` / `backend/src`).

There is **no** separate OCR service and **no** local PostgreSQL — OCR is the
OCR.space (or AWS Textract) API and the database is AWS Aurora PostgreSQL.

## Tests

```bash
cd backend && python -m pytest -q
```
