# ENERVERA — Blood Report Interpretation System

A layered, **knowledge-graph-first** (not black-box ML) pipeline that turns a lab
report PDF/image into validated clinical findings, evidence, and recommendations.

---

## 1. High-level overview

The system is six independent, data-driven layers. Each layer has a typed
input/output contract, so panels and rules are extended by **adding data**
(configs, reference ranges, feature definitions, graph nodes, validation rules)
rather than code. Layers 1–5 are deterministic/clinical logic; Layer 6 is the
only layer that uses an LLM, and strictly as a presentation step.

| Layer                         | Responsibility                                                                                                                                | Lives in                                      | Backing store                    |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- | -------------------------------- |
| **1 — Extraction**            | PDF/image → raw biomarker rows                                                                                                                | `ocr/`                                        | — (PaddleOCR models)             |
| **2 — Normalization**         | raw rows → canonical, unit-standard, reference-anchored, **LOINC-coded** values                                                               | `backend/src/services/normalization/`         | PostgreSQL                       |
| **3 — Feature Generation**    | values → clinical **facts** (binary / severity / ratio) — no disease inference                                                                | `backend/src/services/feature_generation/`    | in-code definitions              |
| **4 — Graph Reasoning**       | **facts + values → inferred diseases**, evidence, conflicts, recommendations (traverses the existing `Biomarker → Threshold → Disease` graph) | `backend/src/services/graph_reasoning/`       | Neo4j (clinical knowledge graph) |
| **5 — Confidence Validation** | findings → validated, calibrated, urgency-flagged, clinician-ready output                                                                     | `backend/src/services/confidence_validation/` | in-code rules                    |
| **6 — LLM Presentation**      | validated findings → patient + clinician reports (LLM, presentation only) + deterministic JSON/HL7/CSV/PDF exports (no LLM)                    | `backend/src/services/presentation/`          | Gemini API (`gemini-2.5-flash`)  |

All **per-panel knowledge** (codes, LOINC, aliases, features, validation rules,
reference ranges) is consolidated in `backend/src/domains/<panel>/` (§3a) so a new
specialty is a folder copy, not a pipeline edit.

**Tech stack:** Python 3.11 · PaddleOCR / pdfplumber / PyMuPDF (L1) ·
PostgreSQL + SQLAlchemy 2 + Alembic (L2) · Neo4j 5 (L4) · Pydantic v2 (contracts)
· Google Gemini API (L6) · FastAPI (API) · pytest / pytest-asyncio (tests).

---

## 2. End-to-end data flow

```
PDF / image
   │
   ▼  Layer 1 — Extraction (OCR-first)
   │   PaddleOCR (scanned/image) · pdfplumber (digital PDF text layer)
   ▼
raw biomarkers:  [{ name, value, unit, confidence }]
   │
   ▼  Layer 2 — Normalization
   │   name → canonical code · unit → standard · reference lookup (PostgreSQL) · quality checks
   ▼
NormalizedBiomarker[]  { value, unit, reference_min/max, status (LOW/NORMAL/HIGH),
                         deviation, critical_flag, quality_issues, loinc_code }
   │
   ▼  Layer 3 — Feature Generation (FACTS ONLY)
   │   binary · severity · ratio   (no disease inference)
   ▼
Layer3Output  { normalized_biomarkers, generated_features, detected_patterns=[] (legacy) }
   │
   ▼  Layer 4 — Graph Reasoning (Neo4j knowledge graph)
   │   Biomarker → (HAS_THRESHOLD/INDICATES/ASSOCIATED_WITH) → Disease/Finding · evidence · recommendations · conflicts
   ▼
Layer4Output  { validated_findings, recommendations, conflicts, audit_trail, status }
   │
   ▼  Layer 5 — Confidence Validation
   │   clinical rule checks · impossibility checks · confidence calibration · severity → urgency
   ▼
Layer5Output  { final_findings, clinical_flags (by urgency), audit_trail,
                clinician_review_required, next_steps, status }
   │
   ▼  Layer 6 — LLM Presentation (Gemini, presentation only)
   │   patient report + clinician report (LLM)  ·  JSON / HL7 / CSV / PDF-metadata (deterministic, no LLM)
   ▼
ReportBundle  { patient_report, clinician_report, exports{json,hl7_v2,csv,pdf_metadata},
                final_findings (unmodified), warnings, status }
```

This flow is driven by the orchestrators (§3): **`CBCFileAnalyzer`** runs the whole
chain from a file (OCR → adapter → L2→L5); **`CBCOrchestrator`** runs L2→L5 from a
`{biomarker_code: value}` dict. The result is a `CBCAnalysisResult` with a nested
per-layer audit trail.

---

## 3. Main components and responsibilities

### Layer 1 — Extraction (`ocr/`)

What the backend pipeline (`CBCFileAnalyzer`) actually uses from `ocr/`:

- `core/stage1_pdf_detector.py` — detect digital-vs-scanned + render pages to images.
- `preprocessing/ocr_engine.py` — PaddleOCR text detection (lazy singleton;
  `truststore`-enabled for the corporate proxy); for digital PDFs pdfplumber reads
  the text layer (`core/stage2_extractor.py`).
- `paddle_ocr_extractor.py` — **PaddleOCRExtractor**: the bridge `CBCFileAnalyzer`
  consumes. `extract_biomarkers_from_file()` renders + OCRs, groups detected text
  into rows (by y), parses each row → `[{name, value, unit, confidence}]`. PaddleOCR
  is imported lazily, so importing this module does not require PaddleOCR installed.

`ocr/api/main.py` is the **OCR microservice** (deployed separately — see §8). Its
**`POST /extract`** runs `PaddleOCRExtractor` and returns the rows; this is what the
backend's `RemoteOCRExtractor` calls in the two-service deployment. (Its older
`POST /analyze` endpoint drives the legacy standalone OCR app below and is not on
the Layers 2–5 path.)

Not used by the backend pipeline (a **separate, self-contained OCR application**,
kept in the repo): `core/stage3_parser`…`stage7_summary` (config-driven parse →
flag → rule-based condition scoring → template summary), `core/pipeline.py`, the
legacy `/analyze` endpoint, and `run_extraction_test.py`. These were built mainly
for the LFT panel and are exercised independently of Layers 2–5.

### Layer 2 — Normalization (`backend/src/services/normalization/`)

- `unit_converter.py` — **UnitConverter** (static): `CONVERSION_FACTORS` per
  biomarker; converts any accepted unit to the standard unit.
- `reference_lookup.py` — **ReferenceRangeLookup** (async): demographic priority
  fallback `exact → no-condition → generic lab → unisex`, indexed on
  `(biomarker_id, gender, age_min, age_max)`.
- `data_quality.py` — **DataQualityChecker** (static): absolute (impossible) and
  critical (panic) value checks.
- `normalizer.py` — **DataNormalizer** (async orchestrator): name→code, unit
  convert, reference lookup, deviation, LOW/NORMAL/HIGH status, quality, and
  **LOINC normalization** (tags each `NormalizedBiomarker.loinc_code` via
  `CODE_TO_LOINC` — the same LOINC the graph uses, so Layer 4 joins by code).
  For a known biomarker it **always emits the canonical standard unit** (e.g. HGB
  `g/dL`) — converting a recognized raw unit (case-insensitively) or assuming the
  standard when OCR omits the unit — so Layer 4's g/dL↔g/L threshold comparison can
  never run on an un-normalized value. Failures are isolated (`validation_issues`).
- DB: `db/models.py` (`ReferenceRange`, `BaseModel`), `db/seeds.py` (idempotent
  seed of reference ranges), `alembic/` (migrations), `db/config.py` + `session.py`.

### Layer 3 — Feature Generation (`backend/src/services/feature_generation/`) — FACTS ONLY

- `feature_definitions.py` — thin re-export shim; the actual feature library now
  lives in **`domains/cbc/features.py`** (see §3a): BINARY (21), SEVERITY (3),
  RATIO (5). The PATTERN (7) + anemia-subtype (5) definitions remain only as legacy
  reference data; they are not used at runtime.
- `feature_generator.py` — **FeatureGenerator** (async): binary flags → severity
  bands → ratios. **No pattern matching / disease inference** — that moved to Layer 4.
- `severity_classifier.py` — **SeverityClassifier** (static): value → band.

### Layer 4 — Graph Reasoning (`backend/src/services/graph_reasoning/`) — KG TRAVERSAL

Reasons directly over the **existing** clinical knowledge graph — no InferenceRule
nodes, no hardcoded disease patterns.

- `neo4j_connection.py` — **Neo4jConnection** (async): lifecycle + generic `query`
  only (a pure transport layer; the Cypher lives in the engine).
- `reasoning_engine.py` — **GraphReasoningEngine** + the three reasoning Cypher
  constants. `reason()` builds biomarker observations from Layer-3 facts (each
  carrying its `loinc_code`), matches graph `Biomarker` nodes by **LOINC code first,
  name/id as fallback**, then per biomarker traverses: **(A)** `HAS_THRESHOLD →
  Threshold → INDICATES → Disease/Finding`
  gated by direction + the threshold's `operator` + a unit-converted (g/dL↔g/L)
  numeric value check; **(B)** direct `INDICATES → Disease/Finding` and **(C)**
  `ASSOCIATED_WITH → Disease` (B/C only for biomarkers with no threshold model, so a
  threshold-modelled marker like HGB stays directional). De-dupes targets, builds
  evidence chains, then recommendations + conflicts; 5 steps, per-step error
  isolation, full audit trail. Does **not** consume `detected_patterns`.
- `biomarker_mappings.py` — **BiomarkerFactMapping**: bridges fact id ↔ canonical
  code (`hemoglobin_low` → `HGB`) ↔ graph `Biomarker.name` (`hemoglobin`). The
  tables themselves now live in **`domains/cbc/biomarkers.py`** (see §3a).

### Layer 5 — Confidence Validation (`backend/src/services/confidence_validation/`)

- `validation_rules.py` — thin re-export shim; the **rule catalogue** now lives in
  **`domains/cbc/validation.py`** (see §3a): `SEVERITY_THRESHOLDS`,
  `CLINICAL_VALIDATION_RULES`, `IMPOSSIBLE_CONDITIONS`, `CONFIDENCE_CALIBRATION`,
  `URGENCY_FLAGS`, the `ClinicalRule` dataclass, and `load_validation_rules()`.
- `confidence_calibrator.py` — **ConfidenceCalibrator**: impossibility checks,
  rule validation, severity classification, and confidence calibration.
- `validation_engine.py` — **ConfidenceValidationEngine** (async orchestrator):
  5-step pipeline, per-step error isolation, clinician-ready output. Recovers
  binary features + biomarker values from the Layer-4 audit trail's embedded
  Layer-3 input (the coupling point between L4 and L5).

### Layer 6 — LLM Presentation (`backend/src/services/presentation/`)

The **only** LLM in the system, used strictly as a presentation layer — it
rephrases/explains validated findings and may **not** create, modify, or invent
findings, diagnoses, confidence values, lab values, recommendations, or guideline
references.

- `prompts.py` — the verbatim patient/clinician system + user templates, plus a
  `GENERAL_SAFETY_PROMPT` appended to every system prompt (so OCR/finding free
  text is treated as **data only**, not instructions).
- `llm_client.py` — `LLMClient` **Protocol** (provider-agnostic) + `GeminiLLMClient`
  (configured provider: `gemini-2.0-flash`, low temperature, safety-block handling,
  `google-genai` SDK, key from `GEMINI_API_KEY`) and `AnthropicLLMClient` (drop-in
  alternative). The SDKs + API keys are loaded **lazily**, so the module imports
  without them and tests inject a fake client.
- `report_generator.py` — **ReportGenerator**: serializes validated Layer-5 data
  into the prompts as delimited data blocks, generates the patient + clinician
  reports (per-report failure isolation → `partial`), and attaches the exports.
  The bulky embedded layer inputs / OCR text in the audit trail are **not**
  forwarded to the model (injection-surface reduction).
- `exports.py` — **deterministic** exporters (no LLM): `to_json`, `to_hl7_v2`
  (ORU^R01), `to_csv`, `to_pdf_metadata` — pure functions of the validated data,
  always produced even if an LLM report fails.

### Contracts (`backend/src/models/`)

- `normalization_schemas.py` — `NormalizedBiomarker`, `PatientMetadata`, …
- `feature_schemas.py` — `GeneratedFeature`, `PatternMatch`, `FeatureGenerationResult`, `Layer3Output`
  (`PatternMatch` is part of the contract but unused at runtime — inference is Layer 4).
- `graph_schemas.py` — `EvidenceLink`, `ValidatedFinding`, `Recommendation`, `ConflictAlert`, `AuditTrail`, `Layer4Output`.
- `validation_schemas.py` — `FinalFinding`, `ClinicalFlag`, `ValidationAuditTrail`, `Layer5Output`.
- `report_schemas.py` — `PatientReport`, `ClinicianReport`, `ReportExports`, `ReportBundle` (Layer 6).

### Orchestration (`backend/src/orchestration/`)

- `cbc_orchestrator.py` — **CBCOrchestrator** (async): chains L2→L5 from a
  `{code: value}` dict; per-layer error isolation (L2 fatal, L3–L5 degrade to
  `partial`); emits `CBCAnalysisResult` + `NestedAuditTrail` (per-layer timings).
  `CBCAnalysisResult` carries `final_findings`, `clinical_flags`, **`recommendations`**
  (surfaced from Layer 4, for Layer 6), and the audit trail. Converts the L2/L3
  **dataclasses** into the Pydantic `Layer3Output` the graph layer consumes.
- `layer1_adapter.py` — **Layer1ToLayer2Adapter** (static) + `OCRRow`: maps OCR
  rows → `{code: value}` by exact / fuzzy (`difflib`) / substring name resolution
  (e.g. `Hemoglobin`/`Hgb`/`RDW-CV` → `HGB`/`RDW`). Its alias table
  (`BIOMARKER_LOOKUP`) is sourced from `domains/cbc/biomarkers.py`.
- `cbc_file_analyzer.py` — **CBCFileAnalyzer** (async): the full file→findings
  pipeline (validate file → OCR → adapter → orchestrator), threading Layer 1 into
  the audit trail. The injected `ocr_extractor` may be the local `PaddleOCRExtractor`
  or the `RemoteOCRExtractor` (same interface).
- `remote_ocr_client.py` — **RemoteOCRExtractor**: drop-in OCR extractor that POSTs
  the file to the OCR microservice's `POST /extract` (via `OCR_SERVICE_URL`) and
  returns the rows, so the backend never imports paddle. Used by `/api/analyze-file`.

### API (`backend/src/api/`)

- `routes.py` — **`POST /api/analyze`**: `AnalyzeCBCRequest` (patient_id,
  `{code: value}` biomarkers, gender, age, metadata) → `CBCOrchestrator.analyze_cbc`
  → `AnalyzeCBCResponse` (status, final_findings, clinical_flags, audit_trail,
  errors, timing). HTTP: 400 (empty / <3 biomarkers or `ValueError`), 503
  (orchestrator/DB not initialized), 500 (unexpected); a graceful `status="error"`
  still returns 200 with the audit trail. Plus `GET /api/health`.
  **`POST /api/analyze-file`** (multipart): upload a PDF/image → the file is sent to
  the OCR microservice (`OCR_SERVICE_URL`), the rows are mapped to canonical codes
  (Layer 1→2 adapter), then run through the same orchestrator; same response shape,
  plus 502 (OCR service unreachable) and 422 (no biomarkers resolved).
  **DI:** the orchestrator + stateless L3–L5 are app-lifetime singletons; the
  `get_db` dependency yields a **fresh `AsyncSession` per request** (an
  `AsyncSession` is not safe to share concurrently), and a registered normalizer
  factory builds a per-request Layer-2 `DataNormalizer` from it, injected into
  `analyze_cbc(..., normalizer=...)`.
- `main.py` — FastAPI app; a **lifespan** startup creates one async engine +
  session factory, registers the normalizer factory, and wires L3–L5 into one
  `CBCOrchestrator` (built with `layer2_normalizer=None` — L2 is per-request).
  DB and Neo4j init are both **best-effort**: a DB failure → `/api/analyze` 503s;
  Neo4j down → Layer 4 degrades to `partial`; neither kills startup. Shutdown
  disposes the engine + Neo4j driver. Run from `backend/src`: `uvicorn api.main:app`.

### 3a. Domains — per-panel knowledge (`backend/src/domains/`)

All knowledge that is **specific to a lab panel** lives here, so the 6-layer
pipeline stays generic and adding a specialty (LFT, Lipid, …) is a folder copy,
not a pipeline edit. (See `domains/README.md` for the step-by-step guide.)

- `base.py` — **`DomainConfig`** dataclass: the contract every panel fulfils
  (required panel, name/LOINC/alias vocab, graph bridge, feature registry,
  validation-rule loader, reference-range rows).
- `registry.py` — **`get_domain(key)`** / `available_domains()`; the single place
  that lists registered specialties.
- `cbc/` — the reference implementation:
  - `biomarkers.py` — `REQUIRED_BIOMARKERS`, `NAME_TO_CODE`, `CODE_TO_NAME`,
    `BIOMARKER_LOOKUP` (OCR aliases), **`CODE_TO_LOINC`**, `FACT_TO_BIOMARKER`,
    `CODE_TO_GRAPH_NAMES`. **Single source of truth** — the normalizer, Layer-1
    adapter, `BiomarkerFactMapping`, and the orchestrator all import from here.
  - `features.py` — Layer-3 `FEATURE_REGISTRY` (the old `feature_definitions.py`).
  - `validation.py` — Layer-5 rule catalogue (the old `validation_rules.py`).
  - `reference_ranges.py` — `reference_range_rows()` consumed by `db.seeds`.
  - `__init__.py` — assembles the `DOMAIN: DomainConfig`.
- `_template/` — copy-to-start scaffold for a new panel (empty tables + a `DOMAIN`).

The former `services/.../feature_definitions.py` and `.../validation_rules.py`
remain as **thin re-export shims** so existing imports keep working.

---

## 4. Neo4j schema (live graph, introspected)

A **full CBC + iron-studies WHO knowledge graph** (not anaemia-only): ~1376
`:Entity` nodes — 593 `Finding`, 183 `Population`, 147 `Threshold`, 142 `Disease`,
87 `Biomarker`, 86 `Recommendation`, 78 `ReferenceRange`, 48 `FollowUpTest`,
8 `Evidence`, 4 `Guideline`. Node `id` = `label::lowercased-name`
(e.g. `biomarker::hemoglobin`, `disease::anaemia`); names are American on the node
("Hemoglobin") but British elsewhere ("haemoglobin"/"anaemia"), and the same
analyte may have several synonym nodes ("Hemoglobin" vs "haemoglobin
concentration"; "RDW" vs "RCDW"). Core CBC nodes carry a **`loinc_code`**
(Hemoglobin `718-7`, Hematocrit `4544-3`, MCV `787-2`, MCH `785-6`, MCHC `786-4`,
Platelets `777-3`) — Layer 4's primary, exact join key (15 of 87 nodes are
LOINC-coded; the rest join by name). Both the extraction side (Layer 2) and the
graph (built by the LOINC-first compiler in `ENERVERA/Graph/cbc`) source the same
LOINC codes.

**Relationships present** (Layer 4 traverses these directly):

```
Biomarker -[HAS_THRESHOLD]->      Threshold      -[INDICATES]-> Disease | Finding
Biomarker -[INDICATES]->          Disease | Finding        (e.g. MCV → iron deficiency)
Biomarker -[ASSOCIATED_WITH]->    Disease                  (e.g. ferritin → iron deficiency anaemia)
Biomarker -[HAS_REFERENCE_RANGE]-> ReferenceRange
Threshold/ReferenceRange -[APPLIES_TO]-> Population
Disease | Finding -[REQUIRES_TEST]-> FollowUpTest
Recommendation -[INDICATES|ASSOCIATED_WITH]-> Disease | Finding
(any) -[SUPPORTED_BY]-> Guideline | Evidence
```

`Threshold` carries `value` (a string, e.g. `"110"`), `operator` (`<`, `<=`, `>=`,
`equal`), and `unit` (e.g. `g/L`). There are **no** `FOLLOWED_BY` / `RECOMMENDS` /
`CONTRADICTS` / `CONFLICTS_WITH` edges (so conflict detection currently returns `[]`).

**CBC coverage:** disease/finding signal spans many biomarkers — HGB→anaemia (via
g/L thresholds, population-specific), MCV/MCH/MCHC→iron deficiency, RDW(RCDW)→
anisocytosis/anemia, Platelets→thrombocytosis/myeloproliferative, Hematocrit→
reduced-in-anemia, ferritin/sTfR/transferrin receptor→iron-deficiency anaemia/
Thalassaemia/megaloblastic anaemia. Adding diseases = adding graph data, no code change.

**Live-verified (2026-06-15):** connected to the configured `NEO4J_URI` and ran
`reason()` end-to-end. Mild IDA (HGB 10.2 g/dL) → anaemia + iron deficiency
(MCV+MCH+MCHC) + anisocytosis + platelet findings + real recommendations; severe
(4.0 g/dL) → adds _severe anaemia_; high Hb → _No anaemia_ only (no false anaemia).
Known limits: direct-edge biomarkers without thresholds (HCT, MCV, PLT) are
non-directional (HCT yields both "reduced in anemia" and "increased in
erythrocytosis"); population-specific threshold selection is not yet applied.

---

## 5. Reasoning & validation workflows (Layers 4–5)

Both orchestrators isolate each step (failure → logged, recorded in
`error_messages`, status `partial`) and emit a full audit trail.

### Layer 4 — graph reasoning (knowledge-graph traversal)

`GraphReasoningEngine.reason(Layer3Output) -> Layer4Output`. It first builds
**biomarker observations** from the Layer-3 facts — each active binary fact →
`{code, names[], ids[], value, unit, loinc, direction}` (via `BiomarkerFactMapping`
+ the `loinc_code` Layer 2 attached) — then in 5 steps:

1. **Infer diseases/findings** (`QUERY_INFER_DISEASES`) — match the graph `Biomarker`
   by **`loinc_code` first, name/id as fallback**, then per biomarker:
   **(A)** `HAS_THRESHOLD → Threshold → INDICATES → Disease/Finding`, gated by the
   fact direction + the threshold's `operator` + a numeric value check with g/dL↔g/L
   unit conversion (so a mild Hb fires _anaemia_, not _severe anaemia_/mortality);
   **(B)** direct `INDICATES → Disease/Finding` and **(C)** `ASSOCIATED_WITH →
Disease`, both restricted to biomarkers with **no** threshold model (across all
   synonym nodes) so threshold-modelled markers stay directional. Targets are
   de-duplicated; each becomes a `ValidatedFinding` (confidence = `0.55 + 0.10 ×
#supporting-biomarkers`, capped 0.95 — multi-marker corroboration scores higher).
2. **Evidence chains** — one `EvidenceLink` per supporting biomarker, e.g.
   `HGB=10.2 g/dL (LOW) supports anaemia [WHO threshold <110 g/L]`.
3. **Recommendations** (`QUERY_INFER_RECOMMENDATIONS`) — `Recommendation
-[INDICATES|ASSOCIATED_WITH]-> target` plus `target -[REQUIRES_TEST]-> FollowUpTest`;
   deduped, ACTIONs before TESTs, capped.
4. **Detect conflicts** (`QUERY_DETECT_CONFLICTS`) — `CONTRADICTS|CONFLICTS_WITH`
   among inferred targets (none in the current graph → `[]`).
5. **Audit trail** — timestamp, queries executed, nodes queried, counts, execution
   time. `layer3_input` is embedded for Layer 5 (the L4↔L5 coupling point).
   `status` is `success` / `no_findings` / `partial` (on a step error).

### Layer 5 — confidence validation

`ConfidenceValidationEngine.validate(Layer4Output) -> Layer5Output`, in 5 steps:

1. **Impossible-condition checks** — flag contradictory combinations
   (e.g. microcytic + macrocytic, high + low hemoglobin) into `clinical_flags["CONFLICTS"]`.
2. **Rule validation** — per finding, check `CLINICAL_VALIDATION_RULES`
   (required present, contradictory absent); failures → `REQUIRES_MANUAL_REVIEW`.
3. **Confidence calibration** — adjust confidence by evidence-count × consistency ×
   severity factors; original→final tracked in the audit trail.
4. **Severity → urgency mapping** — classify each finding's biomarker values
   (`critical/urgent/routine/normal`) and raise `ClinicalFlag`s with escalation
   (`STAT`/hematology_consult, `URGENT`/specialist_review, …).
5. **Final findings + audit trail** — build `FinalFinding`s, group flags by urgency,
   set `clinician_review_required` + `next_steps`.

---

## 6. Current implementation status

Legend: **Tested** = has committed, passing `pytest` tests. **Live-verified** = ran
against the real backing service during development. **Built** = code complete, no
automated test yet. **Not run here** = could not be executed in the current dev
environment (missing dependency / service).

| Area                                                                                                | Status                                                                                                                                                                                                                                                 |
| --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Layer 2 — services (UnitConverter, ReferenceRangeLookup, DataQualityChecker, DataNormalizer)        | ✅ **Tested** — 37 tests (`test_normalization_layer2.py`) against in-memory SQLite. Includes **LOINC normalization** (`domains/cbc/biomarkers.CODE_TO_LOINC` → `NormalizedBiomarker.loinc_code`), verified to tag e.g. HGB `718-7`.                                            |
| Layer 2 — DB (models, idempotent seeds, Alembic)                                                    | ✅ **Built** — `db/models.py` (`ReferenceRange`), `db/seeds.py`, one Alembic migration (`init_reference_range`). ⏳ **Not run here** — migrate + seed on real PostgreSQL is the operator's step (`alembic upgrade head` + `python -m db.seed_runner`). |
| Layer 3 — feature generation (facts only)                                                           | ✅ **Tested** — 26 tests (`test_feature_generation_layer3.py`). Binary/severity/ratio; no disease inference.                                                                                                                                           |
| Layer 4 — graph reasoning (KG traversal, no InferenceRule)                                          | ✅ **Tested** — 15 tests (`test_graph_reasoning_layer4.py`, fake Neo4j). ✅ **Live-verified** against the configured Neo4j (mild/severe/high HGB + multi-marker iron-deficiency returned correct findings + recommendations).                          |
| Layer 5 — validation rules + calibrator + engine                                                    | ✅ **Tested** — 8 tests (`test_validation.py`).                                                                                                                                                                                                        |
| Pydantic contracts (L2–L5)                                                                          | ✅ **Built** — schemas defined and used across the layers (`models/*_schemas.py`).                                                                                                                                                                     |
| Orchestration — `CBCOrchestrator` (L2→L5) + `CBCFileAnalyzer` (file→findings) + `layer1_adapter.py` | ✅ **Built**; per-layer error isolation. ⏳ No committed orchestration test suite; not run end-to-end against live DB+Neo4j in this env.                                                                                                               |
| Layer 1 — `PaddleOCRExtractor` + OCR engine (`ocr/`)                                                | ✅ **Built** (PaddleOCR imported lazily). ⏳ **Not run here** — PaddleOCR is not installed in this dev env, so the OCR→rows path was not executed against a real PDF this session.                                                                     |
| File-analysis CLI — `analyze_report.py`                                                             | ✅ **Built** — runs all six layers (OCR → L2–L5 → Layer 6 reports + exports), flags `--no-reports`/`--model`/`--out-dir`. The **L2→L6 tail is live-verified** (live Neo4j + Gemini). ⏳ The L1 OCR + Postgres front is **not run here** (no PaddleOCR / Postgres in this dev env). |
| FastAPI endpoints — `POST /api/analyze`, **`POST /api/analyze-file`**, `GET /api/health` (`api/routes.py`, `api/main.py`) | ✅ **Built**; per-request `AsyncSession` DI, lifespan wiring, best-effort DB/Neo4j init. `/api/analyze-file` calls the OCR microservice via `RemoteOCRExtractor`. ⏳ No committed API test suite; the file path is not run end-to-end in this env (no OCR service / PaddleOCR). |
| Layer 6 — LLM presentation (`services/presentation/`) | ✅ **Tested** with a fake LLM (`test_presentation_layer6.py`) **and live-verified** against the configured Gemini key (`gemini-2.5-flash`, thinking disabled): patient + clinician reports + exports produced from real graph-derived findings. Provider Gemini (low temperature, safety-block handling, `truststore` for proxy TLS); `AnthropicLLMClient` is a drop-in alternative. |
| Layer 6 — deterministic exports (JSON/HL7v2/CSV/PDF-metadata) | ✅ **Tested** — pure functions over validated data; no LLM; deterministic. |
| **Tests total**                                                                                     | ✅ **102 passing** (`pytest` in `backend/`): L2 37 · L3 26 · L4 15 · L5 8 · L6 16. The `ocr/` suite needs the OCR env and is not run with the backend suite.                                                                                                    |

**Legacy / not in the runtime path** (present in the repo, intentionally unused):
the PATTERN/anaemia-subtype `FeatureDefinition`s in `domains/cbc/features.py`
(disease inference moved to Layer 4); the legacy standalone OCR app
(`ocr/core/stage3–7` + `ocr/api`'s `/analyze` + `ocr/core/pipeline.py`) — the new
extraction path is `PaddleOCRExtractor` + the OCR service's `/extract`.
(`pattern_matcher.py` and `models/schemas.py` have been removed.)

**Graph notes (data, not code):** Layer 4 reasons over whatever is in the live
graph (seeded externally; we only read it via `NEO4J_URI`). Two data-side limits,
both extendable with **zero code change** by adding graph data: direction is
encoded only on `Threshold.operator` (so biomarkers without thresholds — HCT, MCV,
PLT — are non-directional), and there are no conflict edges (`detect_conflicts`
returns `[]`).

**Environment notes:** PaddleOCR vs PyTorch conflict on Windows (OCR runs without
torch); `truststore` needed for OCR model download behind the corporate proxy;
`DATABASE_URL` must use `asyncpg` (app) while `ALEMBIC_DATABASE_URL` uses
`psycopg2` (migrations/seeds). `Neo4jConnection.connect()` falls back `neo4j://` →
`bolt://` (single-instance routing) — set `NEO4J_URI`/`USER`/`PASSWORD` in `.env`.
Layer 6 reads `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) from `.env`/env for the Gemini API.

---

## 7. Future extensibility (CBC, LFT, Lipid, Thyroid, Diabetes)

The architecture is **panel-agnostic**: adding a panel is adding data in one
`domains/<panel>/` folder, not rewriting logic. Copy `domains/_template`, fill in
the four data modules, and register the new `DOMAIN` in `domains/registry.py`
(full walkthrough in `backend/src/domains/README.md`).

| File in `domains/<panel>/` | Feeds layer(s) | What to add for a new panel                                                                                                              |
| -------------------------- | -------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `biomarkers.py`            | 1, 2, 4        | Canonical codes, name aliases (OCR headers), unit/LOINC vocab, the fact→code→graph-name bridge, and the required panel.                  |
| `reference_ranges.py`      | 2              | Reference ranges as seed rows (sex/age/condition-stratified).                                                                           |
| `features.py`              | 3              | `FeatureDefinition`s for the panel: binary thresholds, severity bands, ratios (facts only — no disease patterns).                       |
| `validation.py`            | 5              | Severity thresholds, validation rules, and impossible-condition pairs for the panel's findings.                                         |

Two things still live **outside** the domain folder: the **graph** (Layer 4 reads
`Biomarker → Disease/Finding` edges from Neo4j — add graph data, no code change),
and **Layer 6** (prompts + exporters are panel-agnostic over the validated-findings
contract; adjust prompt wording only if a panel needs different report sections).

Because each layer keys off a canonical biomarker code and reads its knowledge
from the domain config, the same engine serves other panels (LFT, Lipid, Thyroid,
Diabetes) once their domain folder and graph subgraph are added. Multi-panel
reports are handled per biomarker by normalization and feature generation.

---

## 8. Deployment (Render — two microservices)

OCR is heavy (PaddleOCR) and clashes with torch, so it is deployed as a **separate
service** from the backend; the backend reaches it over HTTP.

| Service             | Root      | Start command                                  | Health        | Heavy deps |
| ------------------- | --------- | ---------------------------------------------- | ------------- | ---------- |
| `enervera-ocr`      | `ocr/`    | `uvicorn api.main:app --host 0.0.0.0 --port $PORT` | `/health`     | PaddleOCR  |
| `enervera-backend`  | `backend/`| `uvicorn src.api.main:app --host 0.0.0.0 --port $PORT` | `/api/health` | —          |

- **`render.yaml`** (repo root) declares both services; secrets (`DATABASE_URL`,
  `NEO4J_*`, `GEMINI_API_KEY`, `OCR_SERVICE_URL`) are `sync:false` — set them in the
  Render dashboard, never in git.
- Per-service deps: `ocr/requirements.txt` (paddle, opencv, pdfplumber, pymupdf)
  and `backend/requirements.txt` (no paddle; adds `httpx` for the OCR client +
  `google-genai`). The root `requirements.txt` is the unified set for **local**
  end-to-end runs of `analyze_report.py`.
- Flow: `POST /api/analyze-file` (backend) → `POST /extract` (OCR) → rows → Layer
  1→2 adapter → orchestrator. Set `OCR_SERVICE_URL` to the OCR service's public URL.
- **DB setup** (operator, once): `alembic upgrade head` then `python -m db.seed_runner`
  against PostgreSQL.
- **Secrets:** `.gitignore` excludes `.env`; `.env.example` documents every var.

**Design note (Layer 4 ↔ Layer 5 separation):** Layer 4 emits candidate
biomarker→disease/finding paths from the graph — including non-directional ones
from biomarkers the graph models without thresholds. Layer 5 is the
disambiguation/ranking stage: it calibrates confidence, runs impossible-condition
checks, and maps severity→urgency. This keeps Layer 4 a faithful read of the graph
and concentrates clinical judgement in Layer 5.
