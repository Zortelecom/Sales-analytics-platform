# 🏗️ Sales Analytics Platform

&gt; **A production-grade, local-first data pipeline** that transforms raw Excel sales reports into analytics-ready datasets using Medallion architecture, SQLMesh, and DuckDB.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![DuckDB](https://img.shields.io/badge/DuckDB-OLAP-yellow)](https://duckdb.org/)
[![SQLMesh](https://img.shields.io/badge/SQLMesh-Transformations-green)](https://sqlmesh.com/)
[![Ducklak](https://img.shields.io/badge/Ducklake-Lakehouse-orange)](https://ducklake.select/)
[![Dagster](https://img.shields.io/badge/Dagster-Orchestration-purple)](https://dagster.io/)
[![Streamlit](https://img.shields.io/badge/Streamlit-Reporting-red)](https://streamlit.io/)

---

## 🏛️ Architecture Overview

**The Problem:** Sales teams generate fragmented Excel reports (sales, targets, references) with no consistent schema, making BI integration painful and error-prone.

**The Solution:** A production-grade local-first analytics platform orchestrated with Dagster.
The pipeline ingests Excel files using Python, transforms data with SQLMesh and DuckDB with data quality enforcement,
stores models in DuckLake parquet-backed storage, serves marts through DuckDB,
and powers Streamlit and Power BI dashboards.

![Sales Analytics Platform Architecture](docs/images/architecture.png)

---

## ✨ Key Highlights

| Feature | Implementation |
|---|---|
| **Medallion Architecture** | Bronze (raw seeds) → Silver (staging) → Gold (marts) |
| **Data Quality Gates** | 10+ audits: uniqueness, referential integrity, amount coherence (`qty × price` within 1%) |
| **SCD-Aware Dimensions** | Slowly Changing Dimension logic for clients, products, and salespeople |
| **DuckLake Storage** | Parquet-backed models with environment namespacing (`dev`/`prod`) |
| **Orchestrated DAG** | Dagster assets with file sensors, daily schedules, and asset checks |
| **BI-Ready Serving** | Decoupled DuckDB (`serving.db`) for Streamlit + external tools |
| **Quack Protocol** | Optional client-server mode: zero BI downtime, concurrent connections (DuckDB ≥ v1.5.2) |
| **Export Services** | Async, event-triggered CSV and Parquet exports — optional, independently toggled |
| **Local-First** | Zero cloud dependencies; runs entirely on your machine |

---

## Directory Structure

```
sales-analytics-platform/
├── ingestion/              # Data extraction layer
│   ├── config/             # Settings and source definitions
│   ├── extract/            # Extractor classes (sales, targets, references)
│   ├── load/               # CSV seed writer
│   └── orchestrate/        # File discovery, movement, preprocessing, archival
│
├── sqlmesh/                # Transformation layer
│   ├── seeds/              # CSV files written by ingestion
│   ├── models/
│   │   ├── raw/            # Bronze: seed-loading models
│   │   ├── staging/        # Silver: cleaning & normalization
│   │   └── marts/
│   │       ├── dimensions/ # dim_clientsd, dim_date, dim_salesperson, dim_products
│   │       ├── facts/      # fact_sales, f_targets
│   │       └── reports/    # rep_weekly_meeting, rep_top_products
│   ├── audits/             # Standalone custom audit definitions
│   │   │                   # One AUDIT block per file (SQLMesh requirement).
│   │   │                   # Each returns failing rows, not a count.
│   │   │                   # Referenced by name in model audits() blocks.
│   │   ├── assert_no_orphaned_salesperson.sql   # NULL salesperson_key after SCD join
│   │   ├── assert_no_orphaned_product.sql        # NULL product_key after SCD join
│   │   ├── assert_no_orphaned_client.sql         # NULL clientsd_key after SCD join
│   │   └── assert_amount_matches_qty_x_price.sql # amount deviates >1% from qty×price
│   ├── macros/             # Reusable SQL logic
│   └── tests/              # SQLMesh YAML unit tests
│
├── data/                   # Local data storage (gitignored)
│   ├── input/              # Source Excel files (sales/, targets/, References/)
│   ├── archive/            # Processed Excel files (batch_YYYYMMDD_HHMMSS/)
│   ├── warehouse/          # DuckLake catalog + serving DB + Parquet storage
│   ├── exports/            # CSV and Parquet exports (dev/ and prod/)
│   └── sqlmesh_state.db    # SQLMesh run state
│
├── serving/                # Serving layer
│   ├── sync.py             # Sync marts → serving.db (file-swap or Quack)
│   ├── config.py           # ServingConfig, QuackConfig, ExportConfig
│   ├── cli.py              # CLI interface (sync, serve, export, validate, stats)
│   ├── export/             # Export services (async, event-triggered, optional)
│   │   ├── events.py       # SyncCompletedEvent dataclass
│   │   ├── base_exporter.py    # BaseExporter ABC + ExportResult
│   │   ├── csv_exporter.py     # CsvExporter — for Excel, Power Query, etc.
│   │   ├── parquet_exporter.py # ParquetExporter — for analytics, data science
│   │   ├── event_bus.py    # ExportEventBus (publish_and_wait / publish_background)
│   │   └── __init__.py
│   └── templates/          # BI-friendly view definitions (bi_views.sql)
│
├── orchestration/          # Dagster pipeline
│   ├── assets/             # file_discovery, preprocessing, ingestion, transformation, serving
│   ├── jobs/               # daily_pipeline
│   ├── schedules/          # 6 AM daily schedule
│   ├── sensors/            # File-arrival trigger
│   └── resources/          # DuckDB and SQLMesh resources
│
└── reporting/              # Streamlit dashboards
    ├── pages/              # Executive, Regional, Salesforce, Product, Time Intelligence
    ├── utils/              # DB connection, queries, formatters, filters
    └── Components/         # KPI cards, charts, ranking tables
```

---

## Pipeline Stages

### 1. Ingestion (`ingestion/`)

Handles everything from raw Excel files to CSV seeds.

- **Orchestrate**: `file_discovery.py` scans `data/input/` for new files; `file_mover.py` copies them with validation; `excel_preprocessor.py` strips unwanted sheets from `ExSD-*.xlsx` files; `archive_manager.py` moves processed files to `data/archive/batch_YYYYMMDD_HHMMSS/`
- **Extract**: Typed extractor classes (`sales_extractor`, `target_extractor`, `reference_extractor`) parse each Excel file against expected schemas
- **Load**: `seed_writer.py` writes cleaned data to `sqlmesh/seeds/*.csv` and companion `*_metadata.txt` tracking files

**Seeds produced:**

| Seed File | Source |
|---|---|
| `sales_data.csv` | Sales Excel files |
| `targets_data.csv` | Targets Excel files |
| `clientSD_data.csv` | Client reference |
| `products_data.csv` | Product reference |
| `salesteam_data.csv` | Sales team reference |

### 2. Transformation (`sqlmesh/`)

Medallion architecture running on DuckDB with DuckLake for Parquet-backed storage.

| Layer | Models | Purpose |
|---|---|---|
| **Bronze (Raw)** | `raw_sales`, `raw_targets`, `raw_clientsd`, `raw_products`, `raw_salesteam` | Load CSV seeds verbatim |
| **Silver (Staging)** | `stg_sales`, `stg_targets`, `stg_clientsd_data`, `stg_products`, `stg_salesteam` | Type casting, nulls, deduplication, normalization |
| **Gold (Marts)** | `dim_*`, `fact_sales`, `f_targets` | Star schema: dimensions + facts |
| **Reports** | `rep_weekly_meeting`, `rep_top_products` | Pre-aggregated report views |

Macros (`macros/clean_currency.sql`) handle currency formatting (XAF).

**Audits** (`audits/`) contain standalone custom audit definitions referenced by model `audits (...)` blocks. SQLMesh requires **exactly one `AUDIT` block per file** — each file holds one block and its `SELECT`, which returns failing rows (not a count), making root-cause tracing immediate. SQLMesh resolves audits by name at plan/run time.

| Audit file | Model | Checks |
|---|---|---|
| `assert_no_orphaned_salesperson.sql` | `fact_sales` | No NULL `salesperson_key` after SCD join |
| `assert_no_orphaned_product.sql` | `fact_sales` | No NULL `product_key` after SCD join |
| `assert_no_orphaned_client.sql` | `fact_sales` | No NULL `clientsd_key` after SCD join |
| `assert_amount_matches_qty_x_price.sql` | `fact_sales` | `total_amount` within 1% of `qty × unit_price` |

### 3. Serving (`serving/`)

Copies Gold mart tables from the DuckLake warehouse into `data/warehouse/serving.db`, a standalone DuckDB file that BI tools connect to directly. This decouples the transformation layer from downstream consumers.

Two sync strategies are supported, chosen via `ServingConfig`:

#### File-Swap (default)

The original strategy, improved. Tables are written into a temp DuckDB file using Arrow streaming (replacing the old Pandas round-trip and LIMIT/OFFSET batching), then atomically renamed to `serving.db`. Simple, zero extra dependencies, but BI clients see a brief offline window during the rename.

#### Quack (opt-in, DuckDB ≥ v1.5.2 beta)

A persistent Quack server wraps `serving.db`. The sync process ATTACHes to the live server as a second catalog and rewrites each table with a single SQL statement:

```sql
CREATE OR REPLACE TABLE _serving_remote.bi.fact_sales AS
SELECT * FROM sales_lakehouse.marts__dev.fact_sales
```

No temp file, no rename, no BI outage. Streamlit, Power BI, and ad-hoc DuckDB clients all stay connected during the sync. See [Quack protocol](#quack-protocol) for setup.

#### Export Services (`serving/export/`)

CSV and Parquet exports are fully decoupled from the sync as **independent, async, event-triggered services**. They are completely optional: registering no exporters disables all exports with no config changes.

After a successful sync, `ServingLayerSync` fires a `SyncCompletedEvent`. The `ExportEventBus` dispatches it concurrently to all registered exporters — CSV and Parquet run in parallel, not sequentially.

| Service | File | Purpose |
|---|---|---|
| `CsvExporter` | `export/csv_exporter.py` | UTF-8 CSV files for Excel, Power Query, Pandas |
| `ParquetExporter` | `export/parquet_exporter.py` | Columnar Parquet for analytics, data science |
| `ExportEventBus` | `export/event_bus.py` | Pub/sub wiring; `publish_and_wait` or `publish_background` |
| `SyncCompletedEvent` | `export/events.py` | Immutable event payload |

Two dispatch modes: `publish_and_wait` (Dagster asset waits for exports to finish) and `publish_background` (fire-and-forget daemon thread, pipeline continues immediately).

### 4. Orchestration (`orchestration/`)

Dagster manages the end-to-end pipeline as a DAG of software-defined assets:

```
file_discovery → preprocessing → ingestion → transformation → serving
```

- **Daily schedule**: Runs at 6 AM
- **File sensor**: Optional trigger on new files landing in `data/input/`
- **Resources**: Shared DuckDB connection and SQLMesh context injected into all assets

### 5. Reporting (`reporting/`)

Streamlit multi-page app connecting to `serving.db` (or to the Quack server in Quack mode):

| Page | Content |
|---|---|
| Executive Overview | High-level KPIs and trends |
| Regional Performance | Sales breakdown by region |
| Salesforce Performance | Per-rep metrics vs. targets |
| Product Performance | Revenue by product |
| Time Intelligence | Period-over-period comparisons |

---

## Setup

### Prerequisites

- Python 3.10+
- `pip install -r requirements.txt`

### Configuration

- `ingestion/config/settings.py` — file paths, sheet names, column mappings
- `ingestion/config/sources.yaml` — source definitions per data domain
- `sqlmesh/config.yaml` — SQLMesh project config (DuckDB connection, DuckLake path, environments)
- `serving/config.py` — `ServingConfig` with optional `QuackConfig`

### Running the Pipeline

**Full pipeline via Dagster:**
```bash
cd orchestration
dagster dev
# Open http://localhost:3000 and trigger daily_pipeline
```

**Individual stages:**
```bash
# Ingestion only
python ingestion/main.py

# SQLMesh transformation
cd sqlmesh
sqlmesh run

# Sync to serving DB (file-swap, no exports)
python -m serving.cli sync --env dev

# Sync + CSV export (for Excel users)
python -m serving.cli sync --env dev --csv

# Sync + both formats
python -m serving.cli sync --env dev --csv --parquet

# Reporting app
cd reporting
streamlit run app.py
```

---

## Serving Layer CLI

All serving operations are available through `serving/cli.py`:

```bash
# Sync marts → serving.db
python -m serving.cli sync --env dev
python -m serving.cli sync --env prod --csv --parquet

# Sync with Quack (requires a running Quack server)
python -m serving.cli sync --env dev --quack --quack-token <token>

# One-off export from existing serving.db (no re-sync)
python -m serving.cli export --env dev --csv
python -m serving.cli export --env dev --csv --csv-path /tmp/for-excel/
python -m serving.cli export --env prod --parquet --parquet-compression zstd

# Start a persistent Quack server
python -m serving.cli serve --env dev --quack-token <token>

# Check serving.db is fresh (exit 0 if synced within 24h)
python -m serving.cli validate --env dev

# Print last-sync statistics
python -m serving.cli stats --env dev
```

### Export flags

| Flag | Description |
|---|---|
| `--csv` | Enable CSV export (UTF-8, comma-delimited by default) |
| `--csv-path DIR` | Override CSV output directory |
| `--csv-delimiter CHAR` | Column separator (default: `,`) |
| `--parquet` | Enable Parquet export |
| `--parquet-path DIR` | Override Parquet output directory |
| `--parquet-compression CODEC` | `snappy` (default), `zstd`, `gzip`, `brotli`, `lz4`, `uncompressed` |
| `--export-background` | Fire exports in a background thread; pipeline returns immediately |
| `--export-timeout SECONDS` | Max wait time in blocking mode (default: 300) |

---

## Quack Protocol

Quack turns DuckDB into a client-server database, enabling multiple processes to hold simultaneous read-write connections to the same `serving.db`.

> ⚠️ **Quack is currently in beta.** Stable release is planned for DuckDB v2.0 (September 2026). Use file-swap mode in production until then, or test Quack in your `dev` environment.

### Why Quack improves the serving layer

| Problem (file-swap) | Solution (Quack) |
|---|---|
| Pandas round-trip: `fetch_df → register → CTAS` | Direct DuckDB-to-DuckDB `CREATE OR REPLACE TABLE … AS SELECT` |
| Manual `LIMIT`/`OFFSET` batching | DuckDB vectorised streaming — no Python loop |
| Temp file + atomic rename dance | Sync writes directly to the live server; no temp file |
| BI clients locked out during rename | Server stays live; clients see new data atomically per table |
| Single-writer file lock | Multiple concurrent readers and writers |

### Setup

**1. Install the Quack extension** (requires DuckDB ≥ v1.5.2):
```sql
INSTALL quack FROM core_nightly;
LOAD quack;
```

**2. Start the Quack server** (once, before Streamlit / Power BI):
```bash
python -m serving.cli serve --env dev --quack-token your_secret_token
```

**3. Configure the sync** to use Quack mode:
```python
from serving.config import ServingConfig, QuackConfig

config = ServingConfig(
    environment="dev",
    quack=QuackConfig(host="localhost", port=9494, token="your_secret_token"),
)
config.normalize()
```

**4. Connect BI tools** to the Quack server:
```sql
-- In any DuckDB session (Streamlit, notebook, ad-hoc query):
LOAD quack;
CREATE SECRET (TYPE quack, TOKEN 'your_secret_token');
ATTACH 'quack:localhost:9494' AS serving;
SELECT * FROM serving.bi.fact_sales LIMIT 10;
```

**5. Run the sync** against the live server:
```bash
python -m serving.cli sync --env dev --quack --quack-token your_secret_token
```

---

## Export Services

CSV and Parquet exports are implemented as independent async services under `serving/export/`. They are decoupled from the sync via an event bus — the sync fires a `SyncCompletedEvent` and each registered exporter handles it concurrently.

### Using the export bus in code

```python
from serving.config import ServingConfig
from serving.sync import ServingLayerSync
from serving.export import ExportEventBus, CsvExporter, ParquetExporter

config = ServingConfig(environment="dev")
config.normalize()

# Register only the formats you need — omitting one disables it entirely
bus = (
    ExportEventBus()
    .subscribe(CsvExporter("data/exports/csv/dev"))
    .subscribe(ParquetExporter("data/exports/parquet/dev", compression="zstd"))
)

sync = ServingLayerSync(config, export_bus=bus)
sync.sync()
# → sync runs, then CSV and Parquet export concurrently
```

### Standalone export (no re-sync)

```python
import asyncio
from serving.export import CsvExporter, SyncCompletedEvent

exporter = CsvExporter("data/exports/csv/dev", delimiter=";")  # semicolon for French Excel
event = SyncCompletedEvent(
    environment="dev",
    serving_path="data/warehouse/serving_dev.db",
    bi_schema="bi",
    tables=["fact_sales", "dim_products"],
)
result = asyncio.run(exporter.export(event))
print(result)
```

### Adding a custom exporter

Subclass `BaseExporter` and implement one method:

```python
from serving.export.base_exporter import BaseExporter
from pathlib import Path

class JsonExporter(BaseExporter):
    format = "json"

    def export_table(self, conn, table_name, bi_schema, output_dir):
        out = output_dir / f"{table_name}.json"
        conn.execute(f"""
            COPY {bi_schema}.{table_name}
            TO '{out}' (FORMAT JSON, ARRAY true)
        """)

# Register it like any other exporter:
bus.subscribe(JsonExporter("data/exports/json/dev"))
```

### Encoding note for CSV

DuckDB's `COPY … TO` always writes **UTF-8**. The `ENCODING` option is accepted only on `COPY … FROM` (reading). If a downstream tool requires a different encoding (e.g. latin-1 for legacy Excel on Windows), re-encode the output file after export:

```python
import pathlib, codecs

src = pathlib.Path("data/exports/csv/dev/fact_sales.csv")
dst = pathlib.Path("data/exports/csv/dev/fact_sales_latin1.csv")
dst.write_bytes(src.read_text("utf-8").encode("latin-1", errors="replace"))
```

---

## Data Storage

All data lives under `data/` (gitignored):

| Path | Contents |
|---|---|
| `data/input/` | Source Excel files |
| `data/archive/` | Processed files, batched by timestamp |
| `data/warehouse/catalog.ducklake` | DuckLake catalog |
| `data/warehouse/serving.db` | Serving DuckDB — connect BI tools here |
| `data/warehouse/serving_dev.db` | Dev environment serving DB |
| `data/warehouse/parquet_storage/` | Parquet files per model layer |
| `data/exports/csv/<env>/` | CSV exports per environment |
| `data/exports/parquet/<env>/` | Parquet exports per environment |
| `data/sqlmesh_state.db` | SQLMesh run state |

---

## Development

### Environments

SQLMesh supports `dev` and `prod` environments. Run `sqlmesh run --env dev` during development. Parquet storage is namespaced by environment (`raw__dev.sales/`, `staging__dev.stg_sales/`, etc.). The serving layer mirrors this: `serving_dev.db` for dev, `serving.db` for prod.

### Adding a New Data Source

1. Add an extractor in `ingestion/extract/`
2. Register the source in `ingestion/config/sources.yaml`
3. Add a seed entry in `sqlmesh/seeds/`
4. Create `raw_`, `stg_`, and mart models under `sqlmesh/models/`
5. Add a Dagster asset in `orchestration/assets/`

### Adding a Report Model

1. Create `sqlmesh/models/reports/rep_<name>.sql`
2. Add the corresponding view to `serving/templates/bi_views.sql`
3. Add a Streamlit page under `reporting/pages/`

### Adding a Custom Audit

1. Create `sqlmesh/audits/<audit_name>.sql` with a single `AUDIT (name ...) ; SELECT ... FROM @this WHERE ...` block
2. Reference the audit name inside the target model's `audits (...)` block
3. Run `sqlmesh plan dev` to validate — SQLMesh resolves audits by name automatically

### Adding a Custom Export Format

1. Create `serving/export/<format>_exporter.py`, subclass `BaseExporter`, implement `export_table()`
2. Register an instance on `ExportEventBus` in your pipeline entry point or Dagster asset
3. Optionally add a CLI flag for it in `serving/cli.py`'s `_add_export_args()`

---

## Dependencies

See `requirements.txt` for the full list. Key packages:

| Package | Role |
|---|---|
| `sqlmesh` | SQL transformation framework |
| `duckdb` | Embedded analytical database |
| `ducklake` | Lightweight lakehouse |
| `dagster` | Pipeline orchestration |
| `streamlit` | Reporting dashboards |
| `openpyxl` / `xlrd` | Excel file reading |
| `pandas` | Data manipulation in ingestion |

> **Note:** The Quack extension is not a Python package. Install it inside DuckDB with `INSTALL quack FROM core_nightly` (requires DuckDB ≥ v1.5.2).
