# Domains — adding a new specialty (lab panel)

A **domain** is one lab panel (CBC, LFT, Lipid, Thyroid, …). All knowledge that
is *specific to a panel* lives in its folder under `domains/`. The 6-layer
pipeline is generic — it reads everything it needs through a single
`DomainConfig` object — so **adding a specialty means adding a folder of data
(plus a Neo4j subgraph), not editing the pipeline.**

```
domains/
├── base.py            # DomainConfig — the contract every panel fulfils
├── feature_types.py   # FeatureDefinition — the shared Layer-3 feature contract
├── registry.py        # lists available panels + the merged cross-panel views
├── cbc/               # reference implementation (Complete Blood Count)
│   ├── __init__.py        # builds & exports DOMAIN  (set key/name here)
│   ├── biomarkers.py      # codes, aliases, LOINC, graph-name bridge, required panel
│   ├── features.py        # Layer-3 feature definitions
│   ├── validation.py      # Layer-5 validation rules
│   ├── reference_ranges.py# reference-range seed data
│   ├── units.py           # unit conversions + data-quality limits (Layer 2)
│   └── conditions.py      # clinical interpretation + recommendations (Layer 4)
├── lft/               # Liver Function Test  (same five modules)
├── lipid/             # Lipid Profile        (same five modules)
└── _template/         # copy this folder to start a new panel
```

Registered panels today: **cbc**, **lft**, **lipid**.

> For the full narrative walkthrough with clinical-data prep, examples, and a
> checklist, see [`NEW_SPECIALTY.md`](NEW_SPECIALTY.md). This README is the quick
> reference.

---

## What goes where

| File | Feeds layer(s) | Contents |
|------|----------------|----------|
| `biomarkers.py` | 1, 2, 4 | `REQUIRED_BIOMARKERS`, `NAME_TO_CODE`, `CODE_TO_NAME`, `BIOMARKER_LOOKUP` (OCR aliases), `CODE_TO_LOINC`, `FACT_TO_BIOMARKER`, `CODE_TO_GRAPH_NAMES` |
| `reference_ranges.py` | 2 | `reference_range_rows()` — DB seed rows (age/sex/condition bands) |
| `units.py` | 2 | `load_unit_rules()` — unit-conversion factors + standard units, absolute/critical limits, display names (all keyed by **canonical name**) |
| `conditions.py` | 4 | `load_biomarker_interpretations()` — what each marker means when high/low, both directions, every marker; `load_clinical_conditions()` — multi-marker conditions keyed to `clinical_validation_rules`, with meaning, differential and follow-up |
| `features.py` | 3 | `FEATURE_REGISTRY` — binary / severity / ratio feature definitions (**facts only, no disease inference**) |
| `validation.py` | 5 | severity thresholds, clinical rules, impossible conditions, calibration, urgency flags — returned by `load_validation_rules()` |
| `__init__.py` | — | assembles the five modules into one `DOMAIN: DomainConfig` (set `key` / `name`) |

Two naming rules the pipeline depends on (both are checked by `check_domain` and
the panel test suites):

- **`CODE_TO_NAME` values are globally unique and snake_case.** They are the key
  of the unit/quality tables *and* the prefix of every binary feature id
  (`ALT` → `alt` → `alt_high`). A binary feature whose id is not
  `<name>_low` / `<name>_high` never fires.
- **Canonical codes are globally unique** (`ALT`, `HGB`, `CHOL`, …), because the
  registry merges every panel's tables into one lookup.

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

2. **Fill in the five data modules**, using `domains/cbc/` (haematology) and
   `domains/lft/` / `domains/lipid/` (serum chemistry) as worked examples —
   match their shapes, replace the values with your panel's:
   - `biomarkers.py` — all 7 tables. The LOINC codes in `CODE_TO_LOINC` are the
     **join key to the graph**, so keep them aligned with the graph nodes (step 6).
   - `reference_ranges.py` — return seed rows from `reference_range_rows()`; keep
     the age bands aligned with `ReferenceRangeLookup._get_age_category`
     (CBC uses `(0,18), (18,65), (65,150)`).
   - `features.py` — populate `FEATURE_REGISTRY` (reuse the `FeatureDefinition`
     dataclass from `cbc/features.py`). Facts only — disease inference is Layer 4.
   - `validation.py` — fill the 5 rule sets and return them from
     `load_validation_rules()` (keep the return-dict keys unchanged). Put your
     panel's real escalation path in `URGENCY_FLAGS` — it stays panel-specific.
   - `units.py` — conversion factors + standard units, and the absolute/critical
     limits, all keyed by canonical name.

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

5. **Seed the reference ranges** into PostgreSQL. `db/seeds.py` iterates the
   registry, so a registered panel is seeded automatically — no edit needed:
   ```bash
   cd backend/src
   alembic upgrade head
   python -m db.seed_runner
   ```
   The runner prints a per-panel row count. Seeding is idempotent (rows are
   matched on biomarker + gender + age band + lab + condition and updated in
   place), so re-running after a values change is safe. No migration is required
   for a new panel — `reference_range` is panel-agnostic; set `specimen_type`
   per panel (`Whole Blood` for CBC, `Serum` for LFT/Lipid).

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
those read everything through the `DOMAIN` object and the registry's merged views.

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

### Merged views (multi-panel)

Some stages serve every panel at once — Layer 1 resolves an OCR name without
knowing which panel the report is (and a report may mix panels), and Layer 2's
converter / quality checker are static services. They read the registry's merged
accessors, which union the per-domain tables and **raise on a key two panels
define differently** rather than letting registration order decide:

```python
from domains.registry import (
    merged_name_to_code, merged_code_to_name, merged_code_to_loinc,
    merged_biomarker_lookup, merged_fact_to_biomarker, merged_code_to_graph_names,
    merged_feature_registry, merged_unit_rules, merged_validation_rules,
)
```

`merged_validation_rules()` is the one special case: `severity_thresholds`,
`clinical_validation_rules` and `impossible_conditions` are strict-merged, while
the severity-keyed sets (`urgency_flags`, `confidence_calibration`) are legitimately
panel-specific — CBC escalates to haematology, LFT to hepatology — so the bundle
carries the default panel's table plus every panel's under
`<rule_set>_by_domain`, and Layer 5 routes by the finding's biomarkers.

### Panel detection

```python
from domains.registry import detect_panels

detect_panels(["ALT", "AST", "ALP", "TBIL", "ALB"]).primary   # ["lft"]
```

The orchestrator uses this to enforce **the submitted panel's**
`required_biomarkers` — a lipid profile is complete with CHOL/LDL/HDL/TRIG and is
not rejected for lacking haemoglobin. A mixed report is validated against its
dominant panel; unrecognised codes are reported, not fatal.

---

## Checklist

- [ ] Copied `_template` → `domains/<panel>/`
- [ ] `biomarkers.py` — all 7 tables filled, LOINC codes set, codes + canonical
      names globally unique
- [ ] `reference_ranges.py` — rows returned, age bands aligned, correct units,
      `specimen_type` set
- [ ] `units.py` — conversion factors + standard units, absolute/critical limits
- [ ] `conditions.py` — an interpretation for **every** marker in both directions,
      and a `ClinicalCondition` for every `clinical_validation_rules` entry
      (`check_domain` fails if either drifts). Add `min_severity` to any condition
      defined by a threshold rather than by mere abnormality; `supersedes` when a
      specific reading replaces a generic one; `threshold_defined=True` for a
      definition over measured values (not an inferred cause); `severity_floor`
      when the pattern needs prompt review even with mildly abnormal markers.
      Put each follow-up action once in the `RECS` catalogue and reference it,
      so the same test is never listed twice
- [ ] `features.py` — `FEATURE_REGISTRY` populated (facts only), binary ids follow
      `<canonical name>_low|high`
- [ ] `validation.py` — 5 rule sets + `load_validation_rules()`
- [ ] `__init__.py` — `key` / `name` set, `DOMAIN` exported
- [ ] Registered in `registry.py` (import + dict entry)
- [ ] Neo4j subgraph added with matching `loinc_code`s
- [ ] Reference ranges seeded (`alembic upgrade head` + `db.seed_runner`)
- [ ] Tests pass (`python -m pytest -q`) — mirror `tests/test_domains_lft_lipid.py`
