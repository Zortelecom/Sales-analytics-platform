# Sales Analytics Pipeline - Complete Execution Guide

## Quick Start (TL;DR)

```bash
# 1. Run ingestion (Excel → CSV seeds)
python -m ingestion.main

# 2. Run SQLMesh transformations
cd sqlmesh
sqlmesh plan dev --auto-apply
sqlmesh run

# 3. Query results
duckdb data/sales_analytics.duckdb "SELECT * FROM sales_analytics.mart_executive_summary"
```

---

## Detailed Pipeline Steps

### Phase 1: Data Ingestion (Python)

#### 1.1 Prepare Input Files

Place your Excel files in the correct directories:

```
input_data/
├── sales/
│   ├── ExSD-Sales-Est.xlsx
│   ├── ExSD-Sales-Sud_Ebolowa.xlsx
│   ├── ExSD-Sales-Yde_Centre.xlsx
│   ├── ExSD-Sales-Yde_Nord.xlsx
│   └── ExSD-Sales-Yde_Sud-Ouest.xlsx
├── targets/
│   └── Sales_Targets.xlsx
└── references/
    └── References.xlsx
```

#### 1.2 Run Ingestion Script

```bash
# From project root
python -m ingestion.main
```

**What This Does**:
1. Scans input directories for Excel files
2. Extracts tables starting with "Sales", "Target", "Ref_"
3. Cleans and validates data
4. Writes CSV seeds to `sqlmesh/seeds/`
5. Archives processed Excel files to `archive/batch_YYYYMMDD_HHMMSS/`

**Expected Output**:
```
============================================================
SALES ANALYTICS INGESTION PIPELINE - SEED-BASED
============================================================
Batch ID: batch_20260104_120000
...
✅ Extracted 14,078 sales rows from 5 files
✅ Extracted 475 target rows
✅ Extracted product reference data: 61 rows
✅ Extracted clients SD reference data: 84 rows
✅ Extracted reference data: 24 rows
...
✅ Wrote 14078 rows to sales_data.csv
✅ Wrote 475 rows to targets_data.csv
✅ Wrote 61 rows to products_data.csv
✅ Wrote 84 rows to clientSD_data.csv
✅ Wrote 24 rows to salesteam_data.csv
...
✅ INGESTION COMPLETE
```

#### 1.3 Verify Seeds

```bash
cd sqlmesh/seeds
ls -lh *.csv

# Check row counts
wc -l *.csv
```

---

### Phase 2: Data Transformation (SQLMesh)

#### 2.1 Initialize SQLMesh (First Time Only)

```bash
cd sqlmesh

# Initialize project
sqlmesh init

# Verify configuration
sqlmesh info
```

#### 2.2 Create Development Environment

```bash
# Create and apply plan for dev environment
sqlmesh plan dev --auto-apply
```

**What This Does**:
1. Creates development environment
2. Generates execution plan for all models
3. Shows which models will be created/updated
4. Auto-applies without confirmation (use `--auto-apply`)

#### 2.3 Run Full Transformation

```bash
# Run all models (full refresh)
sqlmesh run
```

**What This Does**:
- Executes models in dependency order:
  1. Seeds (loads CSV data)
  2. Staging models (data cleaning)
  3. Intermediate models (enrichment)
  4. Mart models (analytics)

**Expected Output**:
```
Loading seeds...
✓ sales_analytics.sales_data (14,078 rows)
✓ sales_analytics.targets_data (475 rows)
✓ sales_analytics.products_data (61 rows)
✓ sales_analytics.clientSD_data (84 rows)
✓ sales_analytics.salesteam_data (24 rows)

Running models...
✓ stg_sales (14,078 rows)
✓ stg_targets (475 rows)
✓ stg_products (61 rows)
✓ stg_clients_sd (84 rows)
✓ stg_salesteam (24 rows)
✓ int_sales_enriched (14,078 rows)
✓ int_targets_enriched (475 rows)
✓ int_sales_vs_targets (1,250 rows)
✓ mart_sales_daily (365 rows)
✓ mart_salesperson_performance (288 rows)
✓ mart_product_analysis (1,830 rows)
✓ mart_regional_summary (420 rows)
✓ mart_client_analysis (2,016 rows)
✓ mart_executive_summary (12 rows)

Pipeline completed successfully!
```

#### 2.4 Incremental Updates (Daily Operations)

```bash
# Run for specific date range
sqlmesh run --start-date 2024-12-01 --end-date 2024-12-31

# Run for yesterday only
sqlmesh run --start-date yesterday

# Run for current month
sqlmesh run --start-date $(date -d "$(date +%Y-%m-01)" +%Y-%m-%d)
```

---

### Phase 3: Quality Assurance

#### 3.1 Run Data Quality Audits

```bash
# Run all audits
sqlmesh audit

# Run specific audit
sqlmesh audit stg_sales__no_negative_amounts
```

**Common Audits**:
- ✓ No negative sales amounts
- ✓ No future dates
- ✓ No duplicate SKUs
- ✓ No orphan sales (missing salesperson)
- ✓ Reasonable target achievement

#### 3.2 Verify Model Outputs

```bash
# Check model info
sqlmesh info sales_analytics.mart_sales_daily

# View model SQL
sqlmesh render sales_analytics.int_sales_enriched

# Test specific model
sqlmesh test sales_analytics.stg_sales
```

#### 3.3 Query Results

```bash
# Using SQLMesh CLI
sqlmesh fetchdf "
  SELECT 
    performance_month,
    total_sales,
    total_target,
    achievement_pct
  FROM sales_analytics.mart_executive_summary
  ORDER BY performance_month DESC
  LIMIT 12
"

# Or connect with DuckDB directly
duckdb data/sales_analytics.duckdb
```

---

### Phase 4: Production Deployment

#### 4.1 Promote to Production

```bash
# Create production plan
sqlmesh plan prod

# Review changes
# Press Enter to apply

# Alternative: Auto-apply
sqlmesh plan prod --auto-apply
```

#### 4.2 Schedule Daily Runs

Create a cron job or use a scheduler:

```bash
# Example cron entry (runs at 2 AM daily)
0 2 * * * cd /path/to/project && python -m ingestion.main && cd sqlmesh && sqlmesh run --start-date yesterday
```

#### 4.3 Monitor Pipeline

```bash
# Check recent runs
sqlmesh info

# View model lineage
sqlmesh dag

# Check for failures
tail -f sqlmesh/logs/sqlmesh.log
```

---

## Common Operations

### Check Data Freshness

```sql
-- Latest sales date
SELECT MAX(sale_date) as latest_sale
FROM sales_analytics.stg_sales;

-- Latest mart update
SELECT MAX(performance_month) as latest_month
FROM sales_analytics.mart_executive_summary;
```

### Validate Row Counts

```sql
-- Compare seed vs staging
SELECT 
  'sales_data' as source, COUNT(*) as rows 
FROM sales_analytics.sales_data
UNION ALL
SELECT 
  'stg_sales' as source, COUNT(*) as rows 
FROM sales_analytics.stg_sales;
```

### Check Data Quality Scores

```sql
SELECT 
  sale_month_start,
  AVG(data_quality_score) as avg_quality_score,
  COUNT(*) as total_rows,
  SUM(CASE WHEN has_invalid_date THEN 1 ELSE 0 END) as invalid_dates,
  SUM(CASE WHEN has_invalid_quantity THEN 1 ELSE 0 END) as invalid_quantities
FROM sales_analytics.int_sales_enriched
GROUP BY sale_month_start
ORDER BY sale_month_start DESC;
```

---

## Troubleshooting Guide

### Issue: "No data extracted"

**Cause**: Excel files not in correct format or location  
**Solution**:
1. Check file paths in `config.py`
2. Verify Excel files contain tables with correct prefixes
3. Check sheet names (should be salesperson names for sales files)

```bash
# Verify input files exist
ls -l input_data/sales/
ls -l input_data/targets/
ls -l input_data/references/
```

### Issue: "Missing required columns"

**Cause**: Excel table structure doesn't match expected schema  
**Solution**:
1. Review error message for missing columns
2. Check Excel table headers (case-insensitive, but must match expected names)
3. Verify you're using the latest templates

### Issue: "Seed file not found"

**Cause**: Ingestion didn't complete successfully or seeds in wrong location  
**Solution**:
```bash
# Check if seeds exist
ls -l sqlmesh/seeds/*.csv

# Re-run ingestion
python -m ingestion.main

# Verify SQLMesh config points to correct seed path
cat sqlmesh/config.yaml | grep seeds
```

### Issue: "Model failed to execute"

**Cause**: SQL syntax error or data type mismatch  
**Solution**:
```bash
# View detailed error
sqlmesh render sales_analytics.failing_model

# Test model in isolation
sqlmesh test sales_analytics.failing_model

# Check model dependencies
sqlmesh dag sales_analytics.failing_model
```

### Issue: "Performance is slow"

**Cause**: Large dataset or inefficient queries  
**Solution**:
1. Check if incremental models are running incrementally:
```bash
sqlmesh plan dev --verbose
```

2. Verify date range is reasonable:
```bash
sqlmesh run --start-date 2024-12-01 --end-date 2024-12-31
```

3. Consider partitioning large tables:
```sql
-- Add to model definition
MODEL (
  ...
  partitioned_by (sale_year, sale_month)
)
```

---

## Best Practices

### 1. Data Ingestion
- ✅ Run ingestion during off-hours to avoid file locks
- ✅ Always verify row counts after ingestion
- ✅ Keep archive files for at least 90 days
- ✅ Monitor ingestion logs for warnings

### 2. SQLMesh Operations
- ✅ Use `dev` environment for testing changes
- ✅ Review plans before applying to `prod`
- ✅ Run audits before promoting to production
- ✅ Keep model grain definitions clear and consistent

### 3. Data Quality
- ✅ Review quality scores weekly
- ✅ Investigate sudden drops in data quality
- ✅ Set up alerts for critical audit failures
- ✅ Document data cleaning decisions

### 4. Performance
- ✅ Use incremental models for large tables
- ✅ Partition by time columns
- ✅ Specify grain for all models
- ✅ Only materialize necessary intermediate models

### 5. Maintenance
- ✅ Clean up old archive files monthly
- ✅ Review and update audits quarterly
- ✅ Monitor disk space usage
- ✅ Document model changes in git commits

---

## Performance Benchmarks

Typical execution times (14K sales rows, 5 regions):

| Phase | Operation | Expected Time |
|-------|-----------|---------------|
| Ingestion | Excel extraction | 10-30 seconds |
| Ingestion | CSV writing | 1-5 seconds |
| SQLMesh | Seed loading | 2-5 seconds |
| SQLMesh | Staging models | 5-10 seconds |
| SQLMesh | Intermediate models | 10-20 seconds |
| SQLMesh | Mart models | 15-30 seconds |
| **Total** | **Full pipeline** | **45-100 seconds** |

Incremental runs (1 day of data):

| Phase | Operation | Expected Time |
|-------|-----------|---------------|
| SQLMesh | Incremental models | 5-15 seconds |
| **Total** | **Daily update** | **5-15 seconds** |

---

## Next Steps

### For Analysts
1. Connect BI tool (Tableau, PowerBI, Looker) to DuckDB
2. Create dashboards from mart tables
3. Schedule daily refreshes

### For Data Engineers
1. Set up CI/CD pipeline
2. Implement automated testing
3. Add monitoring and alerting
4. Optimize query performance

### For Business Users
1. Review mart_executive_summary for KPIs
2. Use mart_salesperson_performance for reviews
3. Analyze trends with mart_sales_daily
4. Segment clients with mart_client_analysis

---

## Support & Resources

### Documentation
- SQLMesh Docs: https://sqlmesh.readthedocs.io/
- DuckDB Docs: https://duckdb.org/docs/

### Common Commands Cheat Sheet

```bash
# Ingestion
python -m ingestion.main                    # Run full ingestion

# SQLMesh
sqlmesh plan dev                            # Create plan for dev
sqlmesh run                                 # Run all models
sqlmesh run --start-date yesterday          # Incremental run
sqlmesh audit                               # Run data quality checks
sqlmesh info                                # Show environment info
sqlmesh dag                                 # Show model lineage
sqlmesh fetchdf "SELECT ..."                # Query data
sqlmesh test                                # Run all tests

# DuckDB
duckdb data/sales_analytics.duckdb          # Connect to database
duckdb -c "SELECT * FROM ..." db.duckdb     # Run query from command line
```