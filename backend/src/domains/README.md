# Domains — adding a new specialty (lab panel)

A **domain** is one lab panel (CBC, LFT, Lipid, Thyroid, …). All knowledge that
is *specific to a panel* lives in its folder under `domains/`. The 6-layer
pipeline is generic — it reads everything it needs through a single
`DomainConfig` object — so **adding a specialty means adding a folder of data
(plus a Neo4j subgraph), not editing the pipeline.**

```
domains/
├── base.py            # DomainConfig — the contract every panel fulfils
├── registry.py        # the one place that lists available panels
├── cbc/               # reference implementation (Complete Blood Count)
│   ├── __init__.py        # builds & exports DOMAIN  (set key/name here)
│   ├── biomarkers.py      # codes, aliases, LOINC, graph-name bridge, required panel
│   ├── features.py        # Layer-3 feature definitions
│   ├── validation.py      # Layer-5 validation rules
│   └── reference_ranges.py# reference-range seed data
└── _template/         # copy this folder to start a new panel
```

> For the full narrative walkthrough with clinical-data prep, examples, and a
> checklist, see [`NEW_SPECIALTY.md`](NEW_SPECIALTY.md). This README is the quick
> reference.

---

## What goes where

| File | Feeds layer(s) | Contents |
|------|----------------|----------|
| `biomarkers.py` | 1, 2, 4 | `REQUIRED_BIOMARKERS`, `NAME_TO_CODE`, `CODE_TO_NAME`, `BIOMARKER_LOOKUP` (OCR aliases), `CODE_TO_LOINC`, `FACT_TO_BIOMARKER`, `CODE_TO_GRAPH_NAMES` |
| `reference_ranges.py` | 2 | `reference_range_rows()` — DB seed rows (age/sex/condition bands) |
| `features.py` | 3 | `FEATURE_REGISTRY` — binary / severity / ratio feature definitions (**facts only, no disease inference**) |
| `validation.py` | 5 | severity thresholds, clinical rules, impossible conditions, calibration, urgency flags — returned by `load_validation_rules()` |
| `__init__.py` | — | assembles the four modules into one `DOMAIN: DomainConfig` (set `key` / `name`) |

Two things live **outside** the domain folder and are not a folder copy:

- **The Neo4j graph** (Layer 4) — disease/finding inference is graph data. Add
  `Biomarker → Threshold/Disease/Finding` nodes and edges (with matching LOINC
  codes); no code change. See step 6.
- **Layer 6** (Gemini presentation + deterministic exporters) — panel-agnostic
  over the validated-findings contract. Adjust prompt wording only if a panel
  needs different report sections.

---

## Steps to add a panel (e.g. LFT)

1. **Copy the template:**
   ```bash
   cp -r backend/src/domains/_template backend/src/domains/lft
   ```

2. **Fill in the four data modules**, using `domains/cbc/` as the worked example
   — match its shapes, replace the values with your panel's:
   - `biomarkers.py` — all 7 tables. The LOINC codes in `CODE_TO_LOINC` are the
     **join key to the graph**, so keep them aligned with the graph nodes (step 6).
   - `reference_ranges.py` — return seed rows from `reference_range_rows()`; keep
     the age bands aligned with `ReferenceRangeLookup._get_age_category`
     (CBC uses `(0,18), (18,65), (65,150)`).
   - `features.py` — populate `FEATURE_REGISTRY` (reuse the `FeatureDefinition`
     dataclass from `cbc/features.py`). Facts only — disease inference is Layer 4.
   - `validation.py` — fill the 5 rule sets and return them from
     `load_validation_rules()` (keep the return-dict keys unchanged).

3. **Set `key` and `name`** in `domains/lft/__init__.py` (e.g. `key="lft"`); keep
   the `DOMAIN` export wiring the four modules together.

4. **Register it** in `domains/registry.py` — add two lines:
   ```python
   from domains.lft import DOMAIN as LFT_DOMAIN          # with the other imports

   _REGISTRY: Dict[str, DomainConfig] = {
       CBC_DOMAIN.key: CBC_DOMAIN,
       LFT_DOMAIN.key: LFT_DOMAIN,                        # inside the dict
   }
   ```

5. **Seed the reference ranges** into PostgreSQL. ⚠️ `db/seeds.py` currently
   imports **CBC's** rows directly:
   ```python
   from domains.cbc.reference_ranges import reference_range_rows
   ```
   To seed a new panel, either point that import at your domain, or extend
   `seed_all` to iterate the registry and seed every domain (recommended for
   multi-panel). Then run the operator steps:
   ```bash
   cd backend/src
   alembic upgrade head
   python -m db.seed_runner
   ```

6. **Add the Neo4j subgraph** (Layer 4 inference — data, not code). Create
   `Biomarker` nodes carrying the **same `loinc_code`** as `CODE_TO_LOINC`, plus
   `Disease`/`Finding` nodes and the edges Layer 4 traverses:
   ```
   Biomarker -[HAS_THRESHOLD]-> Threshold -[INDICATES]-> Disease | Finding
   Biomarker -[INDICATES]->     Disease | Finding
   Biomarker -[ASSOCIATED_WITH]-> Disease
   Recommendation -[INDICATES|ASSOCIATED_WITH]-> Disease | Finding
   Disease | Finding -[REQUIRES_TEST]-> FollowUpTest
   ```
   `Threshold` carries `value`, `operator` (`<`, `<=`, `>=`, `equal`), `unit`.
   The graph is built externally (`ENERVERA/Graph/`) and read via `NEO4J_URI`.

7. **Run the tests:**
   ```bash
   cd backend && python -m pytest -q
   ```
   Add a panel-specific test module mirroring the CBC suites to lock in your tables.

**No pipeline / orchestrator / service / API code changes are required** —
those read everything through the `DOMAIN` object.

---

## How the pipeline consumes a domain

The pipeline never imports a panel directly — it asks the registry:

```python
from domains.registry import get_domain, available_domains

cbc = get_domain("cbc")        # case-insensitive; raises KeyError with the
                               # available keys if unknown
get_domain()                   # returns the DEFAULT_DOMAIN ("cbc")
available_domains()            # ["cbc", ...] sorted
```

Layer 4's match order is **`loinc_code` first, then name/id** — which is why
`CODE_TO_LOINC` must agree with the graph nodes' `loinc_code`.

---

## Checklist

- [ ] Copied `_template` → `domains/<panel>/`
- [ ] `biomarkers.py` — all 7 tables filled, LOINC codes set
- [ ] `reference_ranges.py` — rows returned, age bands aligned, correct units
- [ ] `features.py` — `FEATURE_REGISTRY` populated (facts only)
- [ ] `validation.py` — 5 rule sets + `load_validation_rules()`
- [ ] `__init__.py` — `key` / `name` set, `DOMAIN` exported
- [ ] Registered in `registry.py` (import + dict entry)
- [ ] Neo4j subgraph added with matching `loinc_code`s
- [ ] Reference ranges seeded (`alembic upgrade head` + `db.seed_runner`)
- [ ] Tests pass (`python -m pytest -q`)
