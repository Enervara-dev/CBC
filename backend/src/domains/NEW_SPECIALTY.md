# Adding a New Specialty (Lab Panel) — Complete Step-by-Step Process

This guide is the **complete, do-this-in-order** process for adding a new lab
panel (a *specialty* / *domain*) to ENERVERA — e.g. **LFT** (Liver Function),
**Lipid**, **Thyroid**, **Diabetes**.

The architecture is **panel-agnostic**: the 6-layer pipeline reads everything it
needs through one `DomainConfig` object. So **adding a specialty means adding one
folder of data + one graph subgraph — not editing the pipeline, orchestrators,
services, or API.**

> Worked references for every shape below: [`domains/cbc/`](cbc/) (haematology,
> whole blood), [`domains/lft/`](lft/) and [`domains/lipid/`](lipid/) (serum
> chemistry — enzymes, one-sided "desirable" ranges, non-`%`/`g/dL` units).
> Empty scaffold to copy: [`domains/_template/`](_template/).
>
> **Registered today: `cbc`, `lft`, `lipid`.**

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
├── reference_ranges.py  # Layer 2 — DB seed rows
└── units.py             # Layer 2 — unit conversions + quality limits
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

> ⚠️ **Codes and canonical names must be globally unique.** The registry merges
> every panel's tables into one lookup (Layer 1 does not know which panel a
> report is, and a report may mix panels), so a duplicate key raises at import.
> `CODE_TO_NAME` values are also the prefix of your feature ids and the key of
> your unit tables — keep them snake_case (`TBIL` → `total_bilirubin`).

> ⚠️ **LOINC alignment is critical.** Layer 4 matches graph `Biomarker` nodes by
> `loinc_code` first (name/id only as fallback). The LOINC codes here MUST match
> the `loinc_code` on the graph nodes you create in Step 7.

---

## 3. Fill in `reference_ranges.py` (feeds Layer 2)

Return the panel's reference-range seed rows from `reference_range_rows()`. Each
row is a dict matching the `db.models.ReferenceRange` columns. Reuse the `_rr()`
helper pattern from [`cbc/reference_ranges.py`](cbc/reference_ranges.py).

Conventions to keep:
- **Age bands** must line up with `ReferenceRangeLookup._get_age_category` — every panel uses `(0,18), (18,65), (65,150)`.
- Sex-specific rows where clinically needed; unisex rows use `gender=None`.
- Use the panel's conventional units (must match the standard unit the normalizer emits).
- Condition-stratified rows (e.g. `condition="pregnancy"`) are supported.
- Set `specimen_type` for the panel (`Whole Blood` for CBC, `Serum` for LFT/Lipid);
  all panels share the one `reference_range` table, so **no migration is needed**.
- **One-sided ranges are supported and often correct.** `reference_min`/`reference_max`
  may be `None`: the lipid panel caps the atherogenic markers only (never "LOW")
  and floors HDL only (never "HIGH"). See [`lipid/reference_ranges.py`](lipid/reference_ranges.py).

Row shape (from `_rr`): `biomarker_id, gender, age_min, age_max, reference_min,
reference_max, unit, lab_source, condition, specimen_type, source_guideline, version`.

---

## 3b. Fill in `units.py` (feeds Layer 2)

Layer 2's `UnitConverter` and `DataQualityChecker` are static services whose
tables are **merged from the registered panels**, so a panel's units live with the
panel. All five tables are keyed by the **canonical name** (`CODE_TO_NAME` value):

| Table | Purpose |
|-------|---------|
| `CONVERSION_FACTORS` | name → `{unit: factor}`; the factor converts *from* that unit *to* the standard unit, so the standard unit is `1.0`. |
| `STANDARD_UNITS` | name → the standard (factor-1.0) unit the normalizer emits. |
| `ABSOLUTE_LIMITS` | name → `{"min", "max"}` — physiologically possible; outside ⇒ extraction/unit error (`valid=False`). |
| `CRITICAL_VALUES` | name → `{"low", "high"}` — real but life-threatening (`critical_flag=True`). |
| `DISPLAY_NAMES` | name → human label for quality messages. |

Get the factors right per analyte family: cholesterol is 38.67 mg/dL per mmol/L
but triglyceride is 88.57 — sharing one factor misreads a mmol/L report by ~2.3×
(see [`lipid/units.py`](lipid/units.py)).

---

## 4. Fill in `features.py` (feeds Layer 3 — FACTS ONLY)

Define the panel's clinical **facts** and collect them in
`FEATURE_REGISTRY: Dict[str, FeatureDefinition]`. Import the shared
`FeatureDefinition` dataclass from [`feature_types.py`](feature_types.py).

> ⚠️ **Binary feature ids must be `<canonical name>_low` / `<canonical name>_high`.**
> Layer 3 builds the id from the biomarker's LOW/HIGH status and that name
> (`ALT` → `alt` → `alt_high`), so a differently-named definition never fires.

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
- `URGENCY_FLAGS` — severity → urgency/escalation mapping. This one stays
  **panel-specific** (CBC escalates to haematology, LFT to hepatology, lipid to
  the lipid clinic); the registry keeps every panel's table and Layer 5 routes by
  the finding's biomarkers, so use your panel's real escalation path.

---

## 6. Set `key` / `name` and keep the `DOMAIN` export (`__init__.py`)

Edit `domains/<panel>/__init__.py` — change only the `key` and `name`; the rest
just wires the five modules into `DomainConfig`:

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
    unit_rules=load_unit_rules,
    metadata={"specimen_type": "Serum"},   # optional, panel-level notes
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
    LIPID_DOMAIN.key: LIPID_DOMAIN,
}
```

Now `get_domain("lft")` and `available_domains()` see the new panel — and so does
the whole pipeline: registration is what feeds the merged Layer-1 alias table,
the Layer-2 unit/quality tables, the Layer-3 feature library, the Layer-5 rule
bundle, and the seeder. Import fails loudly if your tables disagree with another
panel's (duplicate code, duplicate canonical name, conflicting alias).

Panel detection follows automatically: `detect_panels(codes)` maps the submitted
codes back to their panel, and the orchestrator enforces **that** panel's
`required_biomarkers` — so a lipid profile is not rejected for lacking
haemoglobin.

---

## 9. Seed the reference ranges into the database (Layer 2)

`db.seeds.seed_all` iterates the **registry** and upserts every registered
panel's `reference_range_rows()`, so a registered panel needs no edit here. It is
idempotent: rows are matched on biomarker + gender + age band + lab + condition
and updated in place, so re-running after a values change is safe.

Run the operator steps against PostgreSQL:

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

Add a panel-specific test module to lock in your tables — the closest model is
[`tests/test_domains_lft_lipid.py`](../../../tests/test_domains_lft_lipid.py),
which covers registration, the merged cross-panel tables, the feature-id
convention, seed-row shape, units, panic values, panel detection, seeding, and an
end-to-end Layers 2→3 run. The CBC suites (`test_normalization_layer2.py`,
`test_feature_generation_layer3.py`, `test_graph_reasoning_layer4.py`,
`test_validation.py`) show the per-layer style.

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
- [ ] `biomarkers.py` — all 7 tables filled, LOINC codes set, codes + canonical
      names globally unique and snake_case
- [ ] `reference_ranges.py` — rows returned, age bands aligned, correct units,
      `specimen_type` set
- [ ] `units.py` — conversion factors + standard units, absolute/critical limits,
      display names (all keyed by canonical name)
- [ ] `features.py` — `FEATURE_REGISTRY` populated (facts only, no disease patterns),
      binary ids follow `<canonical name>_low|high`
- [ ] `validation.py` — all 5 rule sets + `load_validation_rules()`, panel's own
      escalation path in `URGENCY_FLAGS`
- [ ] `__init__.py` — `key` / `name` set, `DOMAIN` exported
- [ ] Neo4j subgraph added with matching `loinc_code`s
- [ ] Registered in `registry.py` (import + dict entry)
- [ ] Reference ranges seeded (`alembic upgrade head` + `db.seed_runner`)
- [ ] Tests pass (`python -m pytest -q`)
