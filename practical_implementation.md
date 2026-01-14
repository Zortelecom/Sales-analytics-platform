# Practical Implementation Guide - Sales Analytics

## What You're Building

**Simplified, focused architecture** that stores everything as Parquet in `data/warehouse/parquet/`:

```
Seeds → Dimensions (SCD Type 2) → Facts → Reports
                                          ↓
                              Weekly Meeting Dashboard
                              Top Products Report
                              Target Attainment Report
```

## Key Decisions Made

✅ **SCD Type 2 for dim_product**: Track price and weight changes over time  
✅ **SCD Type 2 for dim_salesperson**: Track territory and role changes  
✅ **No dim_geography for now**: Geographic attributes denormalized in facts  
✅ **Parquet storage**: All data in `data/warehouse/parquet/`  
✅ **3 Business Reports**: Weekly meeting, top products, target attainment  

---

## File Structure

```
project_root/
├── ingestion/                          # Your existing Python code (no changes)
│   ├── extract/
│   ├── load/
│   └── main.py
│
├── sqlmesh/
│   ├── config.yaml                     # ✨ NEW: DuckLake configuration
│   │
│   ├── models/
│   │   ├── seeds/                      # ✨ NEW: Seed model definitions
│   │   │   ├── seed_sales_data.sql
│   │   │   ├── seed_targets_data.sql
│   │   │   ├── seed_products_data.sql
│   │   │   ├── seed_clientsd_data.sql
│   │   │   └── seed_salesteam_data.sql
│   │   │
│   │   ├── dimensions/                 # ✨ NEW: Dimension models
│   │   │   ├── dim_date.sql
│   │   │   ├── dim_product.sql         # SCD Type 2
│   │   │   ├── dim_salesperson.sql     # SCD Type 2
│   │   │   └── dim_client.sql
│   │   │
│   │   ├── facts/                      # ✨ NEW: Fact models
│   │   │   ├── fact_sales.sql
│   │   │   └── fact_targets.sql
│   │   │
│   │   └── reports/                    # ✨ NEW: Business reports
│   │       ├── rep_weekly_meeting.sql
│   │       ├── rep_top_products.sql
│   │       └── rep_target_attainment.sql
│   │
│   └── seeds/                          # CSV files (from ingestion)
│       ├── sales_data.csv
│       ├── targets_data.csv
│       ├── products_data.csv
│       ├── clientSD_data.csv
│       └── salesteam_data.csv
│
└── data/
    └── warehouse/
        ├── catalog.ducklake            # DuckLake catalog
        ├── sqlmesh_state.db            # SQLMesh state
        └── parquet/                    # ✨ All Parquet files here
            ├── dimensions/
            ├── facts/
            └── reports/
```

---

## Step-by-Step Setup

### Step 1: Create Directory Structure

```bash
# From project root
mkdir -p sqlmesh/models/{seeds,dimensions,facts,reports}
mkdir -p data/warehouse/parquet
```

### Step 2: Create config.yaml

Copy from the "Practical SQLMesh Configuration" artifact to `sqlmesh/config.yaml`

### Step 3: Create Seed Models

Create these 5 files in `sqlmesh/models/seeds/`:

**seed_sales_data.sql:**
```sql
MODEL (
  name sales_lakehouse.seeds.sales_data,
  kind SEED (
    path '../seeds/sales_data.csv'
  )
);
```

**seed_targets_data.sql:**
```sql
MODEL (
  name sales_lakehouse.seeds.targets_data,
  kind SEED (
    path '../seeds/targets_data.csv'
  )
);
```

**seed_products_data.sql:**
```sql
MODEL (
  name sales_lakehouse.seeds.products_data,
  kind SEED (
    path '../seeds/products_data.csv'
  )
);
```

**seed_clientsd_data.sql:**
```sql
MODEL (
  name sales_lakehouse.seeds.clientSD_data,
  kind SEED (
    path '../seeds/clientSD_data.csv'
  )
);
```

**seed_salesteam_data.sql:**
```sql
MODEL (
  name sales_lakehouse.seeds.salesteam_data,
  kind SEED (
    path '../seeds/salesteam_data.csv'
  )
);
```

### Step 4: Create Dimension Models

Copy SQL from artifacts into these files:

- `sqlmesh/models/dimensions/dim_date.sql` - From "Practical Dimensions"
- `sqlmesh/models/dimensions/dim_product.sql` - From "dim_product - SCD Type 2"
- `sqlmesh/models/dimensions/dim_salesperson.sql` - From "dim_salesperson - SCD Type 2"
- `sqlmesh/models/dimensions/dim_client.sql` - From "Practical Dimensions"

### Step 5: Create Fact Models

Copy SQL from "Practical Fact Tables" artifact:

- `sqlmesh/models/facts/fact_sales.sql`
- `sqlmesh/models/facts/fact_targets.sql`

### Step 6: Create Report Models

Copy SQL from "Practical Reports" artifact:

- `sqlmesh/models/reports/rep_weekly_meeting.sql`
- `sqlmesh/models/reports/rep_top_products.sql`
- `sqlmesh/models/reports/rep_target_attainment.sql`

---

## Running the Pipeline

### Initial Load (First Time)

```bash
# 1. Run your existing ingestion to create CSV seeds
python -m ingestion.main

# Expected output:
# ✅ Wrote 14078 rows to sales_data.csv
# ✅ Wrote 475 rows to targets_data.csv
# ✅ Wrote 61 rows to products_data.csv
# ✅ Wrote 84 rows to clientSD_data.csv
# ✅ Wrote 24 rows to salesteam_data.csv

# 2. Verify seeds exist
ls -lh sqlmesh/seeds/*.csv

# 3. Initialize SQLMesh and create dev environment
cd sqlmesh
sqlmesh plan dev

# Review the plan, then apply:
# Press Enter or type 'y' to apply

# 4. Run the full pipeline
sqlmesh run

# Expected output:
# ✓ Loading seeds...
# ✓ Building dimensions...
#   - dim_date (3,650 rows)
#   - dim_product (61 rows)
#   - dim_salesperson (24 rows)
#   - dim_client (84 rows)
# ✓ Building facts...
#   - fact_sales (14,078 rows)
#   - fact_targets (475 rows)
# ✓ Building reports...
#   - rep_weekly_meeting (24 rows)
#   - rep_top_products (20 rows)
#   - rep_target_attainment (288 rows)

# 5. Verify Parquet files were created
ls -R ../data/warehouse/parquet/
```

### Daily Operations

```bash
# 1. Run ingestion for new data
python -m ingestion.main

# 2. Run incremental updates
cd sqlmesh
sqlmesh run --start-date yesterday

# This only processes new/changed data
```

---

## Verifying Parquet Storage

### Check File Structure

```bash
tree data/warehouse/parquet/

# Expected output:
# parquet/
# ├── dimensions/
# │   ├── dim_date/
# │   ├── dim_product/
# │   ├── dim_salesperson/
# │   └── dim_client/
# ├── facts/
# │   ├── fact_sales/
# │   └── fact_targets/
# └── reports/
#     ├── rep_weekly_meeting/
#     ├── rep_top_products/
#     └── rep_target_attainment/
```

### Check File Sizes

```bash
du -h data/warehouse/parquet/dimensions/
du -h data/warehouse/parquet/facts/
du -h data/warehouse/parquet/reports/

# Expected sizes (approximate):
# dimensions/ ~250 KB total
# facts/ ~2-5 MB total
# reports/ ~100-500 KB total
```

### Query Parquet Files Directly

```bash
duckdb :memory:

-- Read from Parquet
SELECT COUNT(*) FROM 'data/warehouse/parquet/facts/fact_sales/**/*.parquet';

-- Check schema
DESCRIBE SELECT * FROM 'data/warehouse/parquet/dimensions/dim_product/**/*.parquet';

-- Sample data
SELECT * FROM 'data/warehouse/parquet/reports/rep_weekly_meeting/**/*.parquet' LIMIT 5;
```

---

## Querying Your Data

### Method 1: SQLMesh CLI

```bash
cd sqlmesh

# Weekly meeting report
sqlmesh fetchdf "
  SELECT 
    salesperson_name,
    this_week_sales,
    target_attainment_pct,
    status
  FROM sales_lakehouse.reports.rep_weekly_meeting
  ORDER BY this_week_sales DESC
"

# Top products
sqlmesh fetchdf "
  SELECT 
    product_name,
    total_revenue,
    trend,
    revenue_growth_pct
  FROM sales_lakehouse.reports.rep_top_products
  LIMIT 10
"
```

### Method 2: DuckDB Direct Connection

```bash
duckdb data/warehouse/sqlmesh_state.db
```

```sql
-- Load DuckLake extension
INSTALL ducklake;
LOAD ducklake;

-- Attach lakehouse
ATTACH 'data/warehouse/catalog.ducklake' AS sales_lakehouse (TYPE ducklake);

-- Query reports
SELECT * FROM sales_lakehouse.reports.rep_weekly_meeting;
SELECT * FROM sales_lakehouse.reports.rep_top_products;
SELECT * FROM sales_lakehouse.reports.rep_target_attainment 
WHERE performance_month = '2024-12-01';

-- Query facts
SELECT 
  d.date,
  p.product_name,
  sp.salesperson_name,
  f.sales_amount
FROM sales_lakehouse.facts.fact_sales f
JOIN sales_lakehouse.dimensions.dim_date d ON f.date_key = d.date_key
JOIN sales_lakehouse.dimensions.dim_product p ON f.product_key = p.product_key
JOIN sales_lakehouse.dimensions.dim_salesperson sp ON f.salesperson_key = sp.salesperson_key
WHERE d.date >= '2024-12-01'
LIMIT 100;
```

### Method 3: Python (Jupyter/Scripts)

```python
import duckdb

# Connect to DuckDB
conn = duckdb.connect('data/warehouse/sqlmesh_state.db')

# Load DuckLake
conn.execute("INSTALL ducklake")
conn.execute("LOAD ducklake")
conn.execute("ATTACH 'data/warehouse/catalog.ducklake' AS sales_lakehouse (TYPE ducklake)")

# Query weekly meeting report
df_weekly = conn.execute("""
    SELECT * FROM sales_lakehouse.reports.rep_weekly_meeting
    ORDER BY this_week_sales DESC
""").df()

print(df_weekly)

# Or read Parquet directly
df_parquet = conn.execute("""
    SELECT * FROM 'data/warehouse/parquet/reports/rep_weekly_meeting/**/*.parquet'
""").df()
```

---

## Understanding SCD Type 2

### dim_product (Price/Weight Changes)

**Scenario**: Product price changes on 2024-06-15

```
Before (2024-01-01 to 2024-06-14):
product_key: abc123-v1
sku: COKE500
unit_price: 500
effective_date: 2024-01-01
expiration_date: 2024-06-15
is_current_version: FALSE

After (2024-06-15 onwards):
product_key: abc123-v2
sku: COKE500
unit_price: 550
effective_date: 2024-06-15
expiration_date: NULL
is_current_version: TRUE
```

**Query behavior**:
- Sales on 2024-05-10 join to abc123-v1 (price = 500)
- Sales on 2024-07-20 join to abc123-v2 (price = 550)
- Historical accuracy maintained!

### dim_salesperson (Territory Changes)

**Scenario**: Salesperson moves regions on 2024-08-01

```
Old Assignment (until 2024-07-31):
salesperson_key: sp001-v1
salesperson_id: SP001
region: Centre
effective_date: 2024-01-01
expiration_date: 2024-08-01
is_current_version: FALSE

New Assignment (from 2024-08-01):
salesperson_key: sp001-v2
salesperson_id: SP001
region: Sud
effective_date: 2024-08-01
expiration_date: NULL
is_current_version: TRUE
```

**Query behavior**:
- July sales show region = "Centre"
- August sales show region = "Sud"
- Accurate historical reporting!

---

## Adding dim_geography Later

**When you're ready**, follow the migration guide in the "Practical Dimensions" artifact:

1. Create `dim_geography.sql`
2. Add `geography_key` to `dim_client.sql`
3. Add `geography_key` to `fact_sales.sql`
4. Update reports to use `dim_geography`

**Estimated time**: 2-3 hours  
**No data loss**: Existing data will migrate automatically

---

## Troubleshooting

### Issue: "Could not find DuckLake extension"

```bash
# Install DuckLake manually
duckdb :memory: "INSTALL ducklake FROM community;"
```

### Issue: "Parquet files not found"

**Check:**
```bash
# Verify config path
cat sqlmesh/config.yaml | grep data_path

# Check if directory exists
ls -la data/warehouse/parquet/
```

**Solution:**
```bash
# Create directory if missing
mkdir -p data/warehouse/parquet

# Re-run pipeline
cd sqlmesh
sqlmesh run --full-refresh
```

### Issue: "SCD Type 2 join returns no data"

**Check date ranges:**
```sql
-- Verify salesperson date ranges
SELECT 
  salesperson_id,
  effective_date_key,
  expiration_date_key,
  is_current_version
FROM sales_lakehouse.dimensions.dim_salesperson;

-- Check if sale dates fall within ranges
SELECT 
  f.date_key,
  f.salesperson_id,
  sp.salesperson_key
FROM sales_lakehouse.facts.fact_sales f
LEFT JOIN sales_lakehouse.dimensions.dim_salesperson sp
  ON f.salesperson_id = sp.salesperson_id
  AND f.date_key >= sp.effective_date_key
  AND f.date_key < sp.expiration_date_key
WHERE sp.salesperson_key IS NULL
LIMIT 10;
```

---

## Performance Tips

### 1. Query Parquet Directly for Speed

```sql
-- Faster: Query Parquet directly
SELECT * FROM 'data/warehouse/parquet/reports/rep_weekly_meeting/**/*.parquet';

-- Slower: Go through DuckLake catalog
SELECT * FROM sales_lakehouse.reports.rep_weekly_meeting;
```

### 2. Use Report Tables for Dashboards

Reports are pre-aggregated and optimized:
- `rep_weekly_meeting`: ~24 rows (one per salesperson)
- `rep_top_products`: 20 rows (top products only)
- `rep_target_attainment`: ~300 rows (salesperson × category × month)

Much faster than querying fact_sales (14K+ rows)!

### 3. Partition Strategy

Already configured in facts:
- `fact_sales`: Partitioned by year/month
- `fact_targets`: Partitioned by year

DuckDB automatically uses partitions to skip irrelevant data.

---

## Next Steps

### Week 1: Validate
- ✅ Run full pipeline
- ✅ Verify Parquet files created
- ✅ Check row counts match seeds
- ✅ Test all 3 reports

### Week 2: Dashboard
- Connect Streamlit/Tableau/PowerBI
- Build visualizations from reports
- Add filters (date, region, salesperson)

### Week 3: Production
- Schedule daily runs (cron/Airflow)
- Set up monitoring
- Train business users

### Future Enhancements
- Add dim_geography (when needed)
- Create more reports
- Implement real SCD Type 2 updates
- Add data quality tests

---

## Quick Reference

### Key Commands
```bash
# Ingestion
python -m ingestion.main

# SQLMesh
cd sqlmesh
sqlmesh plan dev
sqlmesh run
sqlmesh run --start-date yesterday
sqlmesh info

# Query
sqlmesh fetchdf "SELECT * FROM sales_lakehouse.reports.rep_weekly_meeting"
duckdb data/warehouse/sqlmesh_state.db
```

### Key Paths
```
CSV Seeds:           sqlmesh/seeds/*.csv
Model Definitions:   sqlmesh/models/**/*.sql
Parquet Storage:     data/warehouse/parquet/
DuckDB State:        data/warehouse/sqlmesh_state.db
DuckLake Catalog:    data/warehouse/catalog.ducklake
```

### Key Tables
```
Dimensions: dim_date, dim_product, dim_salesperson, dim_client
Facts: fact_sales, fact_targets
Reports: rep_weekly_meeting, rep_top_products, rep_target_attainment
```