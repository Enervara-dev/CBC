# Adding a New Specialty (Lab Panel) — Complete Step-by-Step Process

This guide is the **complete, do-this-in-order** process for adding a new lab
panel (a *specialty* / *domain*) to ENERVERA — e.g. **LFT** (Liver Function),
**Lipid**, **Thyroid**, **Diabetes**.

The architecture is **panel-agnostic**: the 6-layer pipeline reads everything it
needs through one `DomainConfig` object. So **adding a specialty means adding one
folder of data + one graph subgraph — not editing the pipeline, orchestrators,
services, or API.**

> Worked reference for every shape below: [`domains/cbc/`](cbc/).
> Empty scaffold to copy: [`domains/_template/`](_template/).

---

## 0. Before you start — gather the panel's clinical data

Collect this for the new panel so the steps below are just data entry:

- **Biomarkers** in the panel + their canonical short codes (e.g. `ALT`, `AST`, `ALP`, `TBIL`).
- **OCR header names / synonyms** that appear on real lab reports for each marker.
- **LOINC codes** for each marker (these are the join key to the Neo4j graph).
- **Reference ranges** (sex / age / condition stratified) + units.
- **Severity / critical (panic) values** per marker.
- **Findings/diseases** the panel should infer (these go in the Neo4j graph, see Step 7).

---

## 1. Copy the template folder

```bash
cp -r backend/src/domains/_template backend/src/domains/<panel>
# example:
cp -r backend/src/domains/_template backend/src/domains/lft
```

You now have:

```
domains/<panel>/
├── __init__.py          # assembles & exports DOMAIN  (edit key/name)
├── biomarkers.py        # Layers 1,2,4 — vocab, LOINC, graph bridge
├── features.py          # Layer 3 — feature definitions
├── validation.py        # Layer 5 — validation/severity/urgency rules
└── reference_ranges.py  # Layer 2 — DB seed rows
```

---

## 2. Fill in `biomarkers.py` (feeds Layers 1, 2, 4)

This is the **single source of truth** for which biomarkers exist and how they're
named. Fill every table (copy the shapes from [`cbc/biomarkers.py`](cbc/biomarkers.py)):

| Table | Purpose | Layer |
|-------|---------|-------|
| `REQUIRED_BIOMARKERS: Tuple[str, ...]` | Codes that must be present to run an analysis. | 2 |
| `NAME_TO_CODE: Dict[str, str]` | extracted name / synonym → canonical code (reference lookup). | 2 |
| `CODE_TO_NAME: Dict[str, str]` | code → lowercase name used by `UnitConverter` / `DataQualityChecker`. | 2 |
| `BIOMARKER_LOOKUP: Dict[str, str]` | **OCR-tolerant** alias → code (lowercase keys; fuzzy/de-spaced matching layered on top in the Layer-1 adapter). | 1 |
| `CODE_TO_LOINC: Dict[str, str]` | code → LOINC code — **shared join key with the Neo4j graph**. | 4 |
| `FACT_TO_BIOMARKER: Dict[str, Union[str, List[str]]]` | Layer-3 fact id → code(s); calculated facts map to a list (e.g. a ratio → its inputs). | 4 |
| `CODE_TO_GRAPH_NAMES: Dict[str, List[str]]` | code → lowercased graph `Biomarker.name` candidates (**exact** match to avoid substring collisions). | 4 |

> ⚠️ **LOINC alignment is critical.** Layer 4 matches graph `Biomarker` nodes by
> `loinc_code` first (name/id only as fallback). The LOINC codes here MUST match
> the `loinc_code` on the graph nodes you create in Step 7.

---

## 3. Fill in `reference_ranges.py` (feeds Layer 2)

Return the panel's reference-range seed rows from `reference_range_rows()`. Each
row is a dict matching the `db.models.ReferenceRange` columns. Reuse the `_rr()`
helper pattern from [`cbc/reference_ranges.py`](cbc/reference_ranges.py).

Conventions to keep:
- **Age bands** must line up with `ReferenceRangeLookup._get_age_category` — CBC uses `(0,18), (18,65), (65,150)`.
- Sex-specific rows where clinically needed; unisex rows use `gender=None`.
- Use the panel's conventional units (must match the standard unit the normalizer emits).
- Condition-stratified rows (e.g. `condition="pregnancy"`) are supported.

Row shape (from `_rr`): `biomarker_id, gender, age_min, age_max, reference_min,
reference_max, unit, lab_source, condition, specimen_type, source_guideline, version`.

---

## 4. Fill in `features.py` (feeds Layer 3 — FACTS ONLY)

Define the panel's clinical **facts** and collect them in
`FEATURE_REGISTRY: Dict[str, FeatureDefinition]`. Import and reuse the
`FeatureDefinition` dataclass from [`cbc/features.py`](cbc/features.py).

Feature types:
- **BINARY** — single biomarker in/out of range. Convention: a `*_low` feature
  stores its cutoff in `threshold_high` (TRUE when `value < threshold_high`); a
  `*_high` feature stores its cutoff in `threshold_low` (TRUE when `value > threshold_low`).
- **SEVERITY** — single biomarker graded into ordered bands.
- **RATIO** — value computed from several biomarkers.

> **Do NOT put disease inference here.** Layer 3 emits facts only; disease/finding
> inference is done by Layer 4 traversing the graph. (CBC's legacy `PATTERN`
> features remain only as unused reference data.)

The fact ids you create here are what `FACT_TO_BIOMARKER` (Step 2) bridges to the graph.

---

## 5. Fill in `validation.py` (feeds Layer 5)

Populate the rule sets and return them from `load_validation_rules()` (keep the
return-dict keys exactly as in the template). Use [`cbc/validation.py`](cbc/validation.py)
as the reference for each shape:

- `SEVERITY_THRESHOLDS` — biomarker → severity band → `{"low": (lo,hi), "high": (lo,hi)}` + flat `"normal": (lo,hi)`. Half-open `[lo, hi)`.
- `CLINICAL_VALIDATION_RULES` — per finding: required-present / contradictory-absent checks (failures → `REQUIRES_MANUAL_REVIEW`).
- `IMPOSSIBLE_CONDITIONS` — contradictory finding pairs (→ `clinical_flags["CONFLICTS"]`).
- `CONFIDENCE_CALIBRATION` — factors used to adjust confidence.
- `URGENCY_FLAGS` — severity → urgency/escalation mapping.

---

## 6. Set `key` / `name` and keep the `DOMAIN` export (`__init__.py`)

Edit `domains/<panel>/__init__.py` — change only the `key` and `name`; the rest
just wires the four modules into `DomainConfig`:

```python
DOMAIN = DomainConfig(
    key="lft",                       # ← machine key (lowercase)
    name="Liver Function Test",      # ← human-readable name
    required_biomarkers=REQUIRED_BIOMARKERS,
    name_to_code=NAME_TO_CODE,
    code_to_name=CODE_TO_NAME,
    code_to_loinc=CODE_TO_LOINC,
    biomarker_lookup=BIOMARKER_LOOKUP,
    fact_to_biomarker=FACT_TO_BIOMARKER,
    code_to_graph_names=CODE_TO_GRAPH_NAMES,
    feature_registry=FEATURE_REGISTRY,
    validation_rules=load_validation_rules,
    reference_range_rows=reference_range_rows,
)
```

---

## 7. Add the graph subgraph in Neo4j (feeds Layer 4 — *data, not code*)

Layer 4 reasons over whatever is in the **live Neo4j graph**. Add the panel's
knowledge as graph data (no code change):

- Create `Biomarker` nodes carrying the **same `loinc_code`** you put in `CODE_TO_LOINC`.
- Add `Disease` / `Finding` nodes and the edges Layer 4 traverses:
  - `Biomarker -[HAS_THRESHOLD]-> Threshold -[INDICATES]-> Disease|Finding`
    (`Threshold` carries `value`, `operator` (`<`,`<=`,`>=`,`equal`), `unit`),
  - `Biomarker -[INDICATES]-> Disease|Finding` (direct),
  - `Biomarker -[ASSOCIATED_WITH]-> Disease`,
  - `Recommendation -[INDICATES|ASSOCIATED_WITH]-> Disease|Finding`, `Disease|Finding -[REQUIRES_TEST]-> FollowUpTest`.
- Markers with a `Threshold` model stay directional; markers with only direct edges are non-directional.

The graph is built externally (see `ENERVERA/Graph/`), then read via `NEO4J_URI`.
Keep LOINC codes aligned with the graph builder so Layer 4 joins by code.

---

## 8. Register the specialty (`registry.py`)

Add **two lines** to [`registry.py`](registry.py):

```python
from domains.lft import DOMAIN as LFT_DOMAIN   # ← top, with the other imports

_REGISTRY: Dict[str, DomainConfig] = {
    CBC_DOMAIN.key: CBC_DOMAIN,
    LFT_DOMAIN.key: LFT_DOMAIN,                 # ← add inside the dict
}
```

Now `get_domain("lft")` and `available_domains()` see the new panel.

---

## 9. Seed the reference ranges into the database (Layer 2)

`db.seeds.seed_all` upserts the rows from a domain's `reference_range_rows()`
(idempotent). Note: today [`db/seeds.py`](../db/seeds.py) imports **CBC's**
`reference_range_rows` directly:

```python
from domains.cbc.reference_ranges import reference_range_rows
```

To seed your new panel as well, either:
- **(a)** point that import at your panel, or
- **(b)** extend `seed_all` to iterate the domain registry and seed every domain's rows (recommended for multi-panel).

Then run the operator steps against PostgreSQL:

```bash
cd backend/src
alembic upgrade head
python -m db.seed_runner
```

---

## 10. Run the tests

```bash
cd backend
python -m pytest -q
```

Add a panel-specific test module mirroring the CBC suites
(`test_normalization_layer2.py`, `test_feature_generation_layer3.py`,
`test_graph_reasoning_layer4.py`, `test_validation.py`) to lock in your tables.

---

## What you do NOT need to touch

These stay generic and require **no change** for a new panel:

- The pipeline services (Layers 1–5), the orchestrators (`CBCOrchestrator`,
  `CBCFileAnalyzer`), the Layer-1 adapter, and the FastAPI routes.
- **Layer 6** (Gemini presentation + deterministic exporters) — panel-agnostic
  over the validated-findings contract. Adjust prompt wording **only** if the
  panel needs different report sections.

---

## Checklist

- [ ] Copied `_template` → `domains/<panel>/`
- [ ] `biomarkers.py` — all 7 tables filled, LOINC codes set
- [ ] `reference_ranges.py` — rows returned, age bands aligned, correct units
- [ ] `features.py` — `FEATURE_REGISTRY` populated (facts only, no disease patterns)
- [ ] `validation.py` — all 5 rule sets + `load_validation_rules()`
- [ ] `__init__.py` — `key` / `name` set, `DOMAIN` exported
- [ ] Neo4j subgraph added with matching `loinc_code`s
- [ ] Registered in `registry.py` (import + dict entry)
- [ ] Reference ranges seeded (`alembic upgrade head` + `db.seed_runner`)
- [ ] Tests pass (`python -m pytest -q`)
