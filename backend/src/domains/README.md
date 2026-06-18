# Domains — adding a new specialty

A **domain** is one lab panel (CBC, LFT, Lipid, …). All knowledge that is
*specific to a panel* lives in its folder under `domains/`. The 6-layer pipeline
is generic — it reads everything it needs through a single `DomainConfig` object,
so **adding a specialty means adding a folder, not editing the pipeline.**

```
domains/
├── base.py            # DomainConfig — the contract every panel fulfils
├── registry.py        # the one place that lists available panels
├── cbc/               # reference implementation (Complete Blood Count)
│   ├── __init__.py        # builds & exports DOMAIN
│   ├── biomarkers.py      # codes, aliases, LOINC, graph-name bridge, required panel
│   ├── features.py        # Layer-3 feature definitions
│   ├── validation.py      # Layer-5 validation rules
│   └── reference_ranges.py# reference-range seed data
└── _template/         # copy this to start a new panel
```

## What goes where

| File | Layer | Contents |
|------|-------|----------|
| `biomarkers.py` | 1–4 | `REQUIRED_BIOMARKERS`, `NAME_TO_CODE`, `CODE_TO_NAME`, `BIOMARKER_LOOKUP` (OCR aliases), `CODE_TO_LOINC`, `FACT_TO_BIOMARKER`, `CODE_TO_GRAPH_NAMES` |
| `features.py` | 3 | `FEATURE_REGISTRY` — binary/severity/ratio/pattern feature definitions |
| `validation.py` | 5 | severity thresholds, clinical rules, impossible conditions, calibration, urgency flags via `load_validation_rules()` |
| `reference_ranges.py` | 2 | `reference_range_rows()` — DB seed rows (age/sex bands) |

## Steps to add a panel (e.g. LFT)

1. **Copy the template:**
   ```bash
   cp -r backend/src/domains/_template backend/src/domains/lft
   ```
2. **Fill in the four data modules.** Use `domains/cbc/` as the worked example —
   match its shapes, replace the values with your panel's.
3. **Set `key` and `name`** in `domains/lft/__init__.py` (e.g. `key="lft"`).
4. **Register it** in `domains/registry.py` — add two lines:
   ```python
   from domains.lft import DOMAIN as LFT_DOMAIN
   # ... inside _REGISTRY:
   LFT_DOMAIN.key: LFT_DOMAIN,
   ```
5. **Seed reference ranges** (if using the DB): `reference_range_rows()` is picked
   up by `db.seeds.seed_all`.
6. **Run the tests:** `cd backend && python -m pytest -q`.

That's it — no pipeline/orchestrator/service code changes are required.

> The LOINC codes in `CODE_TO_LOINC` are the join key to the Neo4j knowledge
> graph (whose `Biomarker` nodes carry the same `loinc_code`). Keep them aligned
> with the graph builder so Layer 4 can match by code, not just by name.
