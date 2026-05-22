# Testing the Sales Analytics Pipeline

A practical reference for running, inspecting, and validating each layer
independently — and then wiring them together through Dagster.

---

## 0. Prerequisites

```bash
# From project root — once
pip install -e ".[dev]"            # installs the package in editable mode + pytest/ruff
cp .env.example .env               # create your local env file
# Edit .env: set SQLMESH_ENV=dev, leave ENABLE_QUACK=false for local testing
```

All commands below assume you are at the **project root** unless stated otherwise.

---

## 1. Ingestion Layer

The ingestion layer is a plain Python process. Test it before anything else —
the seeds it writes are the input to SQLMesh.

### 1a. Dry run (zero side effects)

```bash
python -m ingestion.main --dry-run
```

What it does: discovers files, builds the preprocessing manifest, logs what
*would* happen, then exits. Nothing is moved, written, or archived.
Check the log for:
- how many files were discovered per source type
- whether any files were skipped (size, extension, mtime)
- the preprocessing plan (which sheets would be deleted)

### 1b. Single-source inspection

Run each extractor directly in a Python REPL to inspect output before it
reaches the seed writer:

```python
from ingestion.extract.sales_extractor import SalesExtractor
from ingestion.config.settings import INPUT_PATHS

extractor = SalesExtractor(batch_id="manual_test")
df = extractor.read(INPUT_PATHS["sales"])

print(df.shape)
print(df.dtypes)
print(df[df["has_null_key"] == "True"])   # rows flagged by the null-key fix
print(df["source_file"].value_counts())   # rows per source file
```

Do the same for `TargetExtractor` and `ReferenceExtractor`.

### 1c. Full ingestion run

```bash
python -m ingestion.main
```

After it completes, verify the seeds directory:

```bash
ls -lh sqlmesh/seeds/
# Expect: sales_data.csv, targets_data.csv, clientSD_data.csv,
#         products_data.csv, salesteam_data.csv  + *_metadata.txt siblings
```

Quick row-count sanity check:

```bash
python - <<'EOF'
import pandas as pd, pathlib
for csv in pathlib.Path("sqlmesh/seeds").glob("*.csv"):
    df = pd.read_csv(csv)
    print(f"{csv.name:30s}  {len(df):>7,} rows  {len(df.columns)} cols")
EOF
```

### 1d. Date filter (incremental test)

```bash
# Only process files modified since this date
python -m ingestion.main --since 2025-06-01
```

### 1e. Unit tests (pytest)

```bash
pytest tests/ingestion/ -v
```

The test files from the improvement plan live here:
- `tests/ingestion/test_sales_extractor.py` — null-key flagging, subregion extraction
- `tests/ingestion/test_base_extractor.py` — workbook close in finally, validate_sheet_name default
- `tests/ingestion/test_excel_preprocessor.py` — retry logic, time.sleep behaviour
- `tests/ingestion/test_seed_writer.py` — write/read roundtrip, metadata content

---

## 2. Transformation Layer (SQLMesh)

SQLMesh is run from the `sqlmesh/` subdirectory. Every command below starts
with `cd sqlmesh` or uses `-p sqlmesh`.

### 2a. Validate SQL without writing any data

```bash
cd sqlmesh
sqlmesh plan dev --no-gaps --start 2025-01-01
# SQLMesh parses all models, computes the DAG, shows the diff.
# Press Ctrl-C or answer 'n' to the apply prompt — nothing is written.
```

This catches all syntax errors (including the trailing-comma bug in
`rep_target_attainment.sql`) before a single row is processed.

### 2b. Run YAML unit tests

```bash
cd sqlmesh
sqlmesh test
# Runs all tests in sqlmesh/tests/*.yaml
# Each test injects fixture rows and asserts the SELECT output.
# Zero dependencies on real data — runs in < 1 second.
```

Run a specific test file:

```bash
sqlmesh test tests/test_stg_sales_data.yaml
```

### 2c. Plan and apply for dev

```bash
cd sqlmesh
sqlmesh plan dev --start 2025-01-01 --auto-apply
```

Watch the output for:
- `[WARN]` lines — model had no new intervals, or an audit produced warnings
- `[ERROR]` lines — audit failure; the run stops here
- green `✓` lines — model materialised successfully

### 2d. Run a single model

```bash
cd sqlmesh
sqlmesh plan dev --select staging.stg_sales_data --auto-apply
sqlmesh plan dev --select marts.fact_sales --auto-apply
```

Useful when iterating on a fix for one model without re-running the whole DAG.

### 2e. Run all audits explicitly

```bash
cd sqlmesh
sqlmesh audit --start 2025-01-01
```

Audits are also run automatically during `sqlmesh plan ... --auto-apply`, but
running them standalone gives a cleaner per-audit pass/fail summary without
the plan noise.

### 2f. Inspect model output directly

DuckDB can query the DuckLake catalog directly:

```bash
duckdb data/warehouse/catalog.ducklake
```

```sql
-- Inside the DuckDB shell:
SHOW SCHEMAS;

-- Check row counts across the Gold layer
SELECT 'fact_sales'     AS model, COUNT(*) FROM marts__dev.fact_sales
UNION ALL
SELECT 'fact_targets',           COUNT(*) FROM marts__dev.fact_targets
UNION ALL
SELECT 'dim_products',           COUNT(*) FROM marts__dev.dim_products
UNION ALL
SELECT 'dim_salesperson',        COUNT(*) FROM marts__dev.dim_salesperson
UNION ALL
SELECT 'dim_clientsd',           COUNT(*) FROM marts__dev.dim_clientsd;

-- Verify the BETWEEN bug is fixed (should return 0 after the fix)
SELECT COUNT(*) AS orphaned_product_rows
FROM marts__dev.fact_sales
WHERE product_key IS NOT NULL
  AND sku NOT IN (SELECT sku FROM marts__dev.dim_products WHERE valid_to IS NULL);

-- Spot-check SCD window coverage
SELECT sku, COUNT(*) AS versions, MIN(valid_from), MAX(valid_to)
FROM marts__dev.dim_products
GROUP BY sku
ORDER BY versions DESC
LIMIT 10;
```

### 2g. Validate BI views directly

After `sqlmesh plan dev --auto-apply` has run and the serving sync has
completed (Section 3), open the serving DB and run:

```bash
duckdb data/warehouse/serving_dev.db
```

```sql
-- The critical check for the BETWEEN bug fix
SELECT COUNT(*) AS rows_with_null_product_name FROM v_sales_base
WHERE product_name IS NULL;   -- must be 0

SELECT COUNT(*) AS rows_with_null_client_name FROM v_sales_base
WHERE clientsd_id IS NOT NULL AND client_name IS NULL;  -- must be 0

-- Sanity check the monthly KPI view
SELECT year, month, COUNT(*) AS rows, SUM(revenue), SUM(target)
FROM v_monthly_kpi
GROUP BY year, month
ORDER BY year, month;
```

---

## 3. Serving Layer

### 3a. Sync dev marts to serving_dev.db

```bash
python -m serving.cli sync --env dev
```

Expected output:
```
INFO  Syncing 5 tables from DuckLake → serving_dev.db
INFO  ✓ fact_sales          12,450 rows
INFO  ✓ fact_targets           360 rows
INFO  ✓ dim_products            48 rows
INFO  ✓ dim_salesperson         12 rows
INFO  ✓ dim_clientsd           180 rows
INFO  Sync completed in 0.8s  status=success
```

### 3b. Validate the serving DB

```bash
python -m serving.cli validate --env dev
# Exit 0 = healthy, Exit 1 = validation failed (tables missing, stale, etc.)
```

### 3c. Print sync statistics

```bash
python -m serving.cli stats --env dev
# Shows: last_sync_at, tables_synced, avg_duration_ms, failed_syncs
```

### 3d. On-demand exports

```bash
# CSV only (for Excel users)
python -m serving.cli export --env dev --csv

# Parquet only (for data science / analytics)
python -m serving.cli export --env dev --parquet --parquet-compression zstd

# Both at once
python -m serving.cli export --env dev --csv --parquet
```

Outputs land in `data/exports/csv/dev/` and `data/exports/parquet/dev/`.

### 3e. Sync with exports in one command

```bash
python -m serving.cli sync --env dev --csv --parquet
```

### 3f. Integration test (pytest)

```bash
pytest tests/serving/ -v
# Runs test_serving_sync.py against the fixture DuckLake in tests/fixtures/
```

---

## 4. End-to-End via Dagster

### 4a. Start the Dagster UI

```bash
cd orchestration
dagster dev
# Opens http://localhost:3000
```

### 4b. Run the full pipeline once

In the UI: **Assets** → select all → **Materialize all**

Or from the CLI:

```bash
cd orchestration
dagster asset materialize --select '*'
```

Dagster executes assets in dependency order:
```
current_batch_id
  └── discovered_files
        └── files_to_process
              └── preprocessed_files
                    ├── sales_seed
                    ├── targets_seed
                    └── references_seeds
                          └── seeds_metadata
                                └── sqlmesh_models
                                      └── marts_validation
                                            └── serving_database
                                                  └── pipeline_complete
```

### 4c. Run a specific pipeline stage in isolation

```bash
# Ingestion only (discovery → preprocessing → seeds)
dagster job execute -j ingestion_only_job

# Transformation only (SQLMesh plan + run)
dagster job execute -j transformation_only_job

# Serving only (sync + optional exports)
dagster job execute -j serving_only_job
```

Or materialise a single asset:

```bash
dagster asset materialize --select sqlmesh_models
dagster asset materialize --select serving_database
```

### 4d. Run the data quality check

The `data_quality_full_report` asset check runs automatically after
`fact_sales` is materialised. To trigger it manually:

```bash
dagster asset check execute --select fact_sales
```

In the UI: **Assets** → `fact_sales` → **Checks** tab → run
`data_quality_full_report`. The per-audit breakdown and the path to the
JSON report appear in the **Metadata** panel.

### 4e. Test the scheduled run locally

```bash
# Evaluate the schedule for the next tick (does not execute the job)
dagster schedule preview daily_6am_schedule

# Force a single tick execution right now
dagster schedule execute daily_6am_schedule
```

### 4f. Simulate the file sensor

Drop a new Excel file into `data/source/sales/` and evaluate the sensor:

```bash
dagster sensor evaluate new_file_sensor
# Should report a RunRequest if new files are detected
```

---

## 5. Layered smoke test (copy-paste sequence)

Run these in order after setting up a fresh environment to verify each layer
hands off correctly to the next:

```bash
# 1. Validate SQL (no data written)
cd sqlmesh && sqlmesh plan dev --no-gaps --start 2025-01-01 && cd ..

# 2. Run YAML unit tests
cd sqlmesh && sqlmesh test && cd ..

# 3. Ingest (dry run first, then real)
python -m ingestion.main --dry-run
python -m ingestion.main

# 4. Transform
cd sqlmesh && sqlmesh plan dev --start 2025-01-01 --auto-apply && cd ..

# 5. Audit explicitly
cd sqlmesh && sqlmesh audit --start 2025-01-01 && cd ..

# 6. Sync to serving DB
python -m serving.cli sync --env dev
python -m serving.cli validate --env dev

# 7. Verify BI view fix
duckdb data/warehouse/serving_dev.db -c \
  "SELECT COUNT(*) FROM v_sales_base WHERE product_name IS NULL"
# Must return 0

# 8. Run pytest suite
pytest tests/ -v --tb=short

# 9. Full Dagster run
cd orchestration && dagster asset materialize --select '*'
```

---

## 6. Environment variables for test runs

```bash
# Minimum .env for local dev testing
SQLMESH_ENV=dev
DUCKDB_PATH=data/warehouse/serving_dev.db
ENABLE_QUACK=false
ENABLE_CSV_EXPORT=false
ENABLE_PARQUET_EXPORT=false
EXPORT_BACKGROUND=false

# To test CSV export path
ENABLE_CSV_EXPORT=true
CSV_DELIMITER=,

# To test Parquet export path
ENABLE_PARQUET_EXPORT=true
PARQUET_COMPRESSION=snappy

# To point Dagster at a different env
SQLMESH_ENV=prod
DUCKDB_PATH=data/warehouse/serving.db
```

---

## 7. What a healthy run looks like

| Layer | Key signal | Where to look |
|---|---|---|
| Ingestion | `✓ Wrote N rows to sales_data.csv` | terminal log |
| Ingestion | `has_null_key` count > 0 | WARNING in log (not a failure) |
| SQLMesh plan | `No changes` or green diff | terminal |
| SQLMesh audits | all `PASSED` | `sqlmesh audit` output |
| Serving sync | `status=success` | `serving.cli stats` |
| BI views | `product_name IS NULL` → 0 rows | DuckDB query |
| Dagster | all assets green | UI Assets page |
| Dagster | `data_quality_full_report` → ✔ | UI fact_sales Checks tab |
