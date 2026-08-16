# Sales Analytics Platform

A local-first analytics platform that turns supervisor-maintained Excel
workbooks into a governed warehouse and a set of BI-ready views — for a
consumer-goods distribution business in Cameroon.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![DuckDB](https://img.shields.io/badge/DuckDB-OLAP-yellow)](https://duckdb.org/)
[![DuckLake](https://img.shields.io/badge/DuckLake-Lakehouse-orange)](https://ducklake.select/)
[![SQLMesh](https://img.shields.io/badge/SQLMesh-Transformations-green)](https://sqlmesh.com/)
[![Dagster](https://img.shields.io/badge/Dagster-Orchestration-purple)](https://dagster.io/)
[![Streamlit](https://img.shields.io/badge/Streamlit-Reporting-red)](https://streamlit.io/)

---

## The problem

Sales data arrives as Excel workbooks typed by supervisors in the field. Every
number the business runs on — sell-out by rep, sell-in by sub-distributor,
target attainment, promotional payouts — starts as a hand-entered cell. The
recurring failure is not that the pipeline breaks; it is that a wrong number
looks exactly like a right one, all the way to the dashboard.

So the platform is built around one idea: **every row should be traceable back
to the workbook, the sheet and the position it came from**, and every rule that
can be checked should be checked somewhere a person will actually see it.

## Architecture

```
Excel workbooks
      │  ingestion/ — typed extraction, append-only
      ▼
landing.*            provenance-stamped rows + file_registry
      │  raw.*       views over the CURRENT version of each file
      ▼
staging.*            typing, filtering, deduplication
      │
      ▼
marts.*              SCD2 dimensions + facts  ──────────┐
      │                                                  │
      ▼                                                  ▼
bi.*                 semantic views              meta.*  observability
      │                                                  │
      ├── Streamlit / Superset / Metabase ── attach the lake, read directly
      └── Power BI (Parquet) / Excel (CSV) ── published files
```

Everything lives in one DuckLake catalog. There is no copy of the warehouse
anywhere: interactive tools attach it, and only the two consumers that cannot
speak DuckDB get files.

### The distribution chain

| Tier | Direction | Fact | Source |
|---|---|---|---|
| **Sell-in** | Key Player → Sub-Distributor | `fact_kp_sd` | `ExKP-Destocke_*.xlsx`, `ExKP-NonDestocke.xlsx` |
| **Sell-out** | SD → market, via rep or *demi-gros* | `fact_sales` | `ExSD-Sales-*.xlsx` |

Both tiers side by side is what makes sell-in vs sell-out reconciliation,
overstock signals and promo-attainment measurement possible — an SD's promo
target is checked against what it *bought*, not what it later resold.

---

## Layers

Each layer has one input contract, one output contract, one entry point and one
test command. A layer is independent when it can be developed without running
the one before it — which means being able to *fake* its input.

| Layer | Input | Output | Entry point |
|---|---|---|---|
| 1 Ingestion | Excel | `landing.*` + `file_registry` | `python -m ingestion.main` |
| 2 Transformation | `landing.*` | `marts.*`, `bi.*`, `meta.*` | `sqlmesh plan dev` |
| 3 Serving | `marts.*`, `bi.*` | attach + published files | `python -m serving.publish` |
| 4 Orchestration | — | composes 1–3 | `dagster dev` |

Orchestration is not a fourth *stage*. Dagster composes the three layers and
contains no logic unreachable from a layer's own entry point — if
`orchestration/assets/ingestion.py` and `ingestion/main.py` ever behave
differently, that is the bug.

See `docs/LAYERED_DEVELOPMENT.md`.

---

## Design decisions worth knowing

### Append-only landing

Landing never overwrites. A resubmitted workbook appends; `raw.current_files`
resolves which version is current at read time, by file rather than by row —
an Excel workbook is a complete statement of its own contents, so "newest
ingest of this file wins" needs no per-source natural key.

This is what makes a correction *measurable*. When misfiled destockage rows
were found and the workbooks corrected, the before/after was unrecoverable
under the previous CSV-seed design. Now both versions persist and the fix is
quantifiable: N lines, X XAF, between two dates.

Unchanged files are skipped by sha256, so re-running over an untouched input
directory is a no-op.

### Types are preserved, not stringified

The extractor infers per column: a column with one non-null Python type keeps
it, numbers stored as text are parsed (including French `1 500,50`), and only a
genuinely mixed column falls back to string — and is *reported* when it does,
with row counts and a sample per type. Zero-padded values like `007` stay text,
because the padding is what makes them identifiers.

### Deterministic row identity

Surrogate keys are `SHA1(business key + source file)`, not `uuid4()`. A uuid is
regenerated every run, which is invisible while output is overwritten and fatal
when it is appended: two ingests of the same workbook could not be diffed, and
`fact_kp_sd`'s declared grain was not stable across a restatement.

### SCD Type 2 by LEAD window, not by SQLMesh kind

`SCD_TYPE_2_BY_TIME` expects a *snapshot* — one row per key. The reference
tables are full dated history, several rows per key, so it cannot tell which is
current. `SCD_TYPE_2_BY_COLUMN` works but stamps `valid_from` with the
*execution* time, not the business date.

The source already **is** the history, so nothing needs detecting — only
windowing:

```sql
effective_from AS valid_from,
LEAD(effective_from) OVER (PARTITION BY sd_id ORDER BY effective_from) AS valid_to
```

The earliest version of every key is back-dated to `1900-01-01`: a reference row
describes something that existed before someone typed it in, and without the
floor any fact predating the dimension orphans.

See `docs/SCD_AND_LOOKUPS.md`.

### Three prices, not one

The `Unit_Price` column in a workbook is a VLOOKUP of the traditional-trade
reference price — **not** the price the line was transacted at. So facts carry:

| Column | Meaning |
|---|---|
| `unit_price_sheet` | what the workbook says. Traceability only; never aggregate it. |
| `unit_price_effective` | `total_amount / quantity`. The actual price. |
| `unit_price_standard` | reference for this line's tier and date, from `dim_product_price` |

Tiers are **TT** (traditional trade), **GMS** (grande et moyenne surface) and
**SD** (sell-in). A product absent from a tier is not sold through it.

This is why the old amount audit fired on every GMS and sell-in line: it was
comparing an amount against a price that was never used.

See `docs/PRICING_TIERS.md`.

### Two kinds of data quality

They answer different questions and belong in different places.

**SQLMesh audits** (`sqlmesh/audits/`) enforce rules at build time. A blocking
audit stops a bad build. They report inline on the console.

**Dagster data-quality checks** (`orchestration/assets/data_quality.py`)
attribute problems. They never block. They append to `landing.audit_results` /
`landing.audit_failures` with `source_file`, `sheet_name` and `source_row_num`,
so `meta.workbook_health` can answer the only question a supervisor cares
about: *which file do I open*.

The overlap is deliberate. Where a rule exists in both, SQLMesh's is
authoritative for whether the pipeline proceeds — keep them aligned.

---

## Directory structure

```
sales-analytics-platform/
├── shared/                    imported by every layer
│   ├── paths.py               PROJECT_ROOT-anchored filesystem constants
│   ├── sources.py             THE source registry — only parser of sources.yaml
│   └── env.py                 .env loading, shell-syntax detection
│
├── ingestion/                 LAYER 1
│   ├── main.py                --check / --dry-run / --all / --verify
│   ├── config/                sources.yaml, landing.py, settings.py
│   ├── contracts/             contracts.yaml — declared column schemas
│   ├── extract/               BaseExcelExtractor + one per source
│   ├── load/landing_writer.py append-only writer, file registry, supersession
│   └── orchestrate/           FileDiscovery, ExcelPreprocessor, ArchiveManager
│
├── sqlmesh/                   LAYER 2
│   ├── models/raw/            views over landing, current version only
│   ├── models/staging/        typing, filtering, price unpivot
│   ├── models/marts/          4 SCD2 dimensions, 3 facts
│   ├── models/bi/             17 semantic views — the consumer contract
│   ├── models/meta/           9 observability views
│   └── audits/                13 audits
│
├── serving/publish.py         LAYER 3 — Parquet for Power BI, CSV for Excel
│
├── reporting/                 Streamlit
│   ├── auth/                  Principal, RLS, scrypt passwords, introspect
│   ├── utils/db.py            lake connection, search path, RLS choke point
│   └── pages/
│
├── orchestration/             LAYER 4 — Dagster
│   ├── assets/                discovery → extract → landing → sqlmesh → publish
│   ├── sensors/               new files, pipeline health, prod promotion
│   └── resources/             DuckLakeResource, SQLMeshResource
│
└── data/                      gitignored
    ├── source/  input/  archive/  dead_letter/
    ├── warehouse/             catalog.ducklake + parquet/
    └── exports/               parquet/<env>/, csv/<env>/
```

---

## Setup

```bash
pip install -e ".[dev]"
cp .env.example .env
```

`.env` — the three that matter, and **`DUCKLAKE_CATALOG_PATH` / `PARQUET_PATH`
must match what `sqlmesh/config.yaml` reads**, or ingestion writes to one lake
while SQLMesh reads another. That failure is silent: the `raw.*` views simply
find no tables.

```dotenv
SQLMESH_ENV=dev
DUCKLAKE_CATALOG_PATH=data/warehouse/catalog.ducklake
PARQUET_PATH=data/warehouse/parquet
SQLMESH_STATE_PATH=sqlmesh_state.db
LANDING_SKIP_DUPLICATE_FILES=true
LANDING_EVOLVE_SCHEMA=true
```

Relative paths resolve against the project root. A `.env` is **not** a shell:
`$(pwd)` is literal text, and `ingestion.main --check` rejects it.

Point `ingestion/config/sources.yaml` at your synced source folder.

---

## Running

### Layer by layer

```bash
# 1 — ingestion
python -m ingestion.main --check       # config only, writes nothing
python -m ingestion.main --all         # first load: every file, ignoring mtime
python -m ingestion.main --verify      # what is in landing right now

# 2 — transformation
cd sqlmesh
sqlmesh create_external_models         # register landing.* — do not skip
sqlmesh plan dev --start 2024-10-01
sqlmesh audit --start 2024-10-01

# 3 — serving
python -m serving.publish --env dev --verify
python -m serving.publish --env dev --parquet --csv

# reporting
streamlit run reporting/app.py
```

⚠️ `--all` matters on a first load: `check_for_new_files` defaults to a
**24-hour** window, which is right for a scheduled run and wrong for a backfill.

⚠️ `sqlmesh create_external_models` is not optional. Without it the `raw.*`
views still build, but column-level lineage stops at the landing boundary and
`plan` cannot warn when a staging model expects a column landing no longer has.

### Orchestrated

```bash
dagster dev --working-directory orchestration
dagster asset materialize --select '*'
```

### Tests

```bash
pytest tests/shared tests/ingestion -v   # no lake, no Excel, no network
cd sqlmesh && sqlmesh test
pytest tests/serving -v
```

Layer 2 can be developed against a synthetic lake, no Excel required:

```bash
python -m tests.fixtures.make_landing_fixture --path /tmp/fixture.ducklake --force
DUCKLAKE_CATALOG_PATH=/tmp/fixture.ducklake sqlmesh plan dev
```

The fixture is deliberately awkward — an SD switching destockage status
mid-history, two spellings of one KP, a superseded file, a retired file, a null
`sale_date`. A fixture full of clean rows tests nothing.

---

## Observability

`meta.*` is nine views over `landing.file_registry` and the landing tables. No
new infrastructure — observability is just another mart, so it dashboards like
everything else.

| View | Answers |
|---|---|
| `ingestion_batches` | Did the last run work? |
| `source_files` | Which workbook arrived when, how stale, resubmitted how often |
| `landing_inventory` | Current vs total vs **superseded** rows |
| `column_inventory` | Schema drift |
| `freshness` | Latest *business date* vs latest *file arrival* |
| `extraction_coverage` | Rows per sheet — catches a tab someone cleared |
| `audit_results` | Audit outcomes, with `is_regression` |
| `audit_failures` | Failing rows, with workbook and sheet |
| `workbook_health` | **Which file needs attention**, ordered by impact |

Two earn their place beyond the obvious. `freshness` separates a file that
arrived *late* from one that arrived on time containing *nothing new* — only
the second is invisible everywhere else. `extraction_coverage` exists because a
sheet someone cleared produces a structurally valid table with one row and no
error anywhere.

---

## Security

Streamlit is gated by scrypt-hashed passwords in `.streamlit/secrets.toml`,
with lockout, session timeout and role-based page access.

Row-level security is enforced in **one** place — `reporting/utils/db.py`'s
`query()` — by SQL rewriting, so no page including the LLM-driven "Ask Your
Data" can bypass it. Scope is per `Principal` across region, subregion,
salesperson, supervisor, channel and client; the query cache is partitioned by
scope fingerprint so two users with different visibility cannot collide.

`python -m reporting.auth.introspect` audits the scope map against the live
lake. Run it after changing anything under `sqlmesh/models/bi/` — those views
are rebuilt by `sqlmesh plan`, so a column an RLS mapping depends on can vanish
without anyone editing `reporting/`.

⚠️ Threat model: a shared-secret gate for trusted colleagues on a LAN. Serve it
over HTTPS or passwords cross the network in clear text.

---

## Known limits and next steps

**A DuckDB-file catalog admits many readers or one writer.** So Streamlit
cannot attach while `sqlmesh plan` runs, and `concurrent_tasks: 1` is set in
`sqlmesh/config.yaml`. **A PostgreSQL catalog removes both** and is the
prerequisite for Superset or Metabase reading the lake concurrently. It is the
single highest-value next change — see `docs/LAYER3_SERVING.md` phase B.

**SQLMesh state is a DuckDB file committed to git.** Fine for one developer,
broken for two: it is a binary that cannot merge and grows with every plan.
Move it to Postgres at the same time.

**RLS will need consolidating before a second BI tool.** Superset and Metabase
each have their own mechanism; three implementations of one rule will drift.
Scoped `bi.v_*` views are the fix, decided *before* the second tool goes live.

**Two audit definitions overlap by design** — `sqlmesh/audits/` and
`data_quality.py`. Keep them aligned; a rule that disagrees between the two is
worse than a rule enforced once.

**`unit_price_gms` / `unit_price_sd` must be back-dated in `Ref_Products`.**
Tier prices added with today's date leave every historical line with a NULL
`unit_price_standard`.

**Workbook fixes that would remove whole classes of error:**
`SD_name` in the ExSD files should be derived by formula rather than typed;
the `SD_id` lookup in `ExKP-NonDestocke.xlsx` is the only place a hand-typed
value determines a foreign key, and deserves a blocking validation; date-aware
`LOOKUP` formulas keep supervisors' own pivot tables agreeing with the reports.

---

## Dependencies

| Package | Role |
|---|---|
| `duckdb` + `ducklake` | storage and query |
| `sqlmesh[duckdb]` | transformation, audits, lineage |
| `dagster` (+ `dagster-webserver`) | orchestration — pin as a group |
| `streamlit` | reporting |
| `openpyxl` | Excel reading |
| `pydantic` / `pydantic-settings` | typed configuration |
| `pandas` | ingestion dataframes |
| `pytest`, `ruff` | tests and linting |

> The DuckLake extension is not a Python package — DuckDB installs it on first
> connect.
