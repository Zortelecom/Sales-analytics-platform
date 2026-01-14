# SeedWriter Module - Implementation Guide

## 🎯 What Changed

**Before:**
- CSV writing logic mixed in `main.py`
- Harder to test and reuse
- Less modular

**After:**
- Clean `SeedWriter` class in `load/seed_writer.py`
- Reusable for any DataFrame → CSV workflow
- Easy to test and extend
- Better separation of concerns

## 📦 Files to Create/Update

### 1. Create `ingestion/load/seed_writer.py` ⭐ NEW

Copy the "seed_writer.py - CSV Seed Writer Module" artifact.

**What it provides:**
```python
from load.seed_writer import SeedWriter

# Initialize writer
writer = SeedWriter(
    seeds_dir=Path("sqlmesh/seeds"),
    batch_id="batch_20260102_120000"
)

# Write single seed
path = writer.write_seed(df_sales, 'sales_data')

# Write multiple seeds
paths = writer.write_seeds({
    'sales_data': df_sales,
    'targets_data': df_targets
})

# Get summary
summary = writer.get_seed_summary()

# Cleanup old metadata
writer.cleanup_old_seeds(keep_latest=5)
```

### 2. Update `ingestion/main.py`

Replace with the updated "Modified main.py" artifact.

**Key changes:**
```python
# OLD: Manual CSV writing
def write_csv_seed(df, output_path, batch_id):
    df_clean = df.drop(columns=['source_file', ...])
    df_clean.to_csv(output_path, index=False)
    # ... metadata handling ...

# NEW: Use SeedWriter
from load.seed_writer import SeedWriter

seed_writer = SeedWriter(SQLMESH_SEEDS_DIR, batch_id)
written_paths = seed_writer.write_seeds({
    'sales_data': df_sales,
    'targets_data': df_targets
})
```

### 3. Create `ingestion/load/__init__.py`

```python
"""
Load module - Handles writing data to various destinations
"""
from .seed_writer import SeedWriter

__all__ = ['SeedWriter']
```

## 🚀 Quick Implementation (5 Minutes)

```powershell
# Navigate to project
cd C:\Users\User\projects\data-engineering-labs\sales-analytics-platform-v0.3

# 1. Create the new module
New-Item -ItemType File -Path "ingestion\load\seed_writer.py" -Force
# Copy content from artifact above

# 2. Create __init__.py
New-Item -ItemType File -Path "ingestion\load\__init__.py" -Force
# Add: from .seed_writer import SeedWriter

# 3. Update main.py
# Replace with updated version from artifact

# 4. Test it
python -m ingestion.main
```

## ✅ Expected Output

```
==================================================================
SALES ANALYTICS INGESTION PIPELINE - SEED-BASED
==================================================================
Batch ID: batch_20260102_120530
Timestamp: 2026-01-02T12:05:30.123456+00:00
==================================================================

[PHASE 1/3] EXTRACTION
--------------------------------------------------
Extracting sales data...
Processing Sales file: sample_sales_jan_mar_2024.xlsx
Found table: SalesJan_Mar_2024
Extracted 500 rows
✅ Extracted 500 sales records

Extracting targets data...
Processing Target file: sales_targets_2024.xlsx
Found table: Target_2024
Extracted 30 rows
✅ Extracted 30 target records

[PHASE 2/3] WRITING CSV SEEDS
--------------------------------------------------
✅ Wrote 500 rows to sales_data.csv
✅ Wrote 30 rows to targets_data.csv
📄 Seed ready: sqlmesh\seeds\sales_data.csv
📄 Seed ready: sqlmesh\seeds\targets_data.csv

[PHASE 3/3] ARCHIVING SOURCE FILES
--------------------------------------------------
Archived: sample_sales_jan_mar_2024.xlsx → data\archive\batch_20260102_120530\sample_sales_jan_mar_2024.xlsx
Archived: sales_targets_2024.xlsx → data\archive\batch_20260102_120530\sales_targets_2024.xlsx
✅ Archived 2 Excel file(s)

==================================================================
✅ INGESTION COMPLETE
==================================================================
Batch ID................. batch_20260102_120530
Sales Records............ 500
Target Records........... 30
Seeds Written............ 2
Files Archived........... 2
Seeds Directory.......... sqlmesh\seeds

==================================================================
NEXT STEPS:
==================================================================
1. Review CSV seeds in: sqlmesh/seeds/
2. Run SQLMesh transformations:
   cd sqlmesh
   sqlmesh plan dev
3. Launch dashboard:
   streamlit run demo_dashboard.py
==================================================================

Seed Summary:
  sales_data:
    - Rows: 500
    - Size: 0.08 MB
    - Modified: 2026-01-02 12:05:30
  targets_data:
    - Rows: 30
    - Size: 0.00 MB
    - Modified: 2026-01-02 12:05:30
```

## 🔍 Verify Implementation

### Check Seed Files Created
```powershell
dir sqlmesh\seeds\

# Should show:
# sales_data.csv
# sales_data_metadata.txt
# targets_data.csv
# targets_data_metadata.txt
```

### Check Metadata File
```powershell
Get-Content sqlmesh\seeds\sales_data_metadata.txt
```

Expected content:
```
Seed Metadata
==================================================

Seed Name: sales_data
Batch ID: batch_20260102_120530
Timestamp: 2026-01-02T12:05:30.123456+00:00
Rows Written: 500
Columns: 12

Column Names:
  - sales_line_id
  - date
  - sku
  - product_name
  - qty
  - unit_price
  - total_amount
  - salesperson_id
  - salesperson_name
  - region
  - customer_id
  - customer_name

Source Files:
  - sample_sales_jan_mar_2024.xlsx

Ingestion Batch IDs:
  - batch_20260102_120530
```

### Check CSV Content
```powershell
Get-Content sqlmesh\seeds\sales_data.csv -Head 5
```

Expected:
```csv
sales_line_id,date,sku,product_name,qty,unit_price,total_amount,salesperson_id,salesperson_name,region,customer_id,customer_name
abc123-xyz,2024-01-15,PROD001,Laptop HP ProBook,2,450 000 XAF,900 000 XAF,SP001,Jean Dupont,CENTRE,CUST001,TechCorp SARL
def456-uvw,2024-01-16,PROD002,Mouse Logitech,5,15 000 XAF,75 000 XAF,SP002,Marie Kouassi,LITTORAL,CUST002,Digital Services
...
```

## 📚 SeedWriter API Reference

### Class: `SeedWriter`

#### Constructor
```python
SeedWriter(seeds_dir: Path, batch_id: str)
```
- `seeds_dir`: Directory where CSV seeds will be written
- `batch_id`: Unique identifier for this ingestion run

#### Methods

##### `write_seed()`
Write a single DataFrame to CSV seed.

```python
path = writer.write_seed(
    df=df_sales,
    seed_name='sales_data',
    metadata_columns=['source_file', 'ingestion_ts']  # Optional
)
```

**Returns:** `Path` to written CSV file

**Raises:** `ValueError` if DataFrame is empty

##### `write_seeds()`
Write multiple seeds at once.

```python
paths = writer.write_seeds({
    'sales_data': df_sales,
    'targets_data': df_targets
})
```

**Returns:** `Dict[str, Path]` mapping seed_name → file path

**Note:** Continues on error (doesn't fail entire batch)

##### `cleanup_old_seeds()`
Remove old metadata files.

```python
writer.cleanup_old_seeds(keep_latest=5)
```

**Args:**
- `keep_latest`: Number of metadata files to keep per seed (default: 5)

**Note:** Only removes metadata files, not actual CSV seeds

##### `get_seed_summary()`
Get information about all seeds in directory.

```python
summary = writer.get_seed_summary()
# {
#   'sales_data': {
#     'path': Path('sqlmesh/seeds/sales_data.csv'),
#     'size_mb': 0.08,
#     'rows': 500,
#     'modified': datetime(2026, 1, 2, 12, 5, 30)
#   },
#   ...
# }
```

**Returns:** `Dict[str, Dict]` with seed statistics

## 🎯 Use Cases

### Use Case 1: Standard Ingestion (Your Current Flow)
```python
# In main.py
seed_writer = SeedWriter(SQLMESH_SEEDS_DIR, batch_id)
written_paths = seed_writer.write_seeds({
    'sales_data': df_sales,
    'targets_data': df_targets
})
```

### Use Case 2: Custom Metadata Exclusion
```python
# Keep some metadata in the seed
path = seed_writer.write_seed(
    df=df_sales,
    seed_name='sales_with_source',
    metadata_columns=['ingestion_ts', 'ingestion_batch_id']  # Only exclude these
)
# Result: source_file and sheet_name will remain in CSV
```

### Use Case 3: Error Handling
```python
try:
    paths = seed_writer.write_seeds(seeds_dict)
    logger.info(f"Successfully wrote {len(paths)} seeds")
except Exception as e:
    logger.error(f"Failed to write seeds: {e}")
    # Handle error (retry, alert, etc.)
```

### Use Case 4: Monitoring & Reporting
```python
# After writing seeds
summary = seed_writer.get_seed_summary()

for seed_name, info in summary.items():
    if info['size_mb'] > 10:
        logger.warning(f"Large seed file: {seed_name} ({info['size_mb']:.2f} MB)")
    
    if info['rows'] == 0:
        logger.error(f"Empty seed: {seed_name}")
```

### Use Case 5: Automated Cleanup
```python
# Keep only last 10 runs of metadata
seed_writer.cleanup_old_seeds(keep_latest=10)

# Or cleanup after every run
seed_writer.write_seeds(seeds_dict)
seed_writer.cleanup_old_seeds()
```

## 🧪 Testing

### Manual Testing
```python
# Create test DataFrame
import pandas as pd
from pathlib import Path
from load.seed_writer import SeedWriter

df_test = pd.DataFrame({
    'id': [1, 2, 3],
    'name': ['Alice', 'Bob', 'Charlie'],
    'value': [100, 200, 300],
    'ingestion_ts': ['2026-01-01', '2026-01-01', '2026-01-01']
})

# Write seed
writer = SeedWriter(Path('test_seeds'), 'test_batch_001')
path = writer.write_seed(df_test, 'test_data')

print(f"Wrote seed to: {path}")
print(f"Seed exists: {path.exists()}")

# Verify content
import pandas as pd
df_read = pd.read_csv(path)
print(df_read)
# Should NOT have 'ingestion_ts' column
```

### Unit Test Example
```python
import unittest
from pathlib import Path
import pandas as pd
from load.seed_writer import SeedWriter

class TestSeedWriter(unittest.TestCase):
    
    def setUp(self):
        self.test_dir = Path('test_seeds')
        self.test_dir.mkdir(exist_ok=True)
        self.writer = SeedWriter(self.test_dir, 'test_batch')
    
    def test_write_seed_removes_metadata(self):
        df = pd.DataFrame({
            'id': [1],
            'name': ['Test'],
            'ingestion_ts': ['2026-01-01']
        })
        
        path = self.writer.write_seed(df, 'test')
        df_read = pd.read_csv(path)
        
        self.assertIn('id', df_read.columns)
        self.assertIn('name', df_read.columns)
        self.assertNotIn('ingestion_ts', df_read.columns)
    
    def tearDown(self):
        # Cleanup test files
        import shutil
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
```

## 🎨 Customization Examples

### Custom SeedWriter Subclass
```python
from load.seed_writer import SeedWriter

class CompressedSeedWriter(SeedWriter):
    """Write compressed CSV seeds to save space."""
    
    def write_seed(self, df, seed_name, metadata_columns=None):
        # Call parent to get cleaned DataFrame
        path = super().write_seed(df, seed_name, metadata_columns)
        
        # Compress the CSV
        import gzip
        with open(path, 'rb') as f_in:
            with gzip.open(f"{path}.gz", 'wb') as f_out:
                f_out.writelines(f_in)
        
        # Remove uncompressed version
        path.unlink()
        
        return Path(f"{path}.gz")
```

### Add Data Validation
```python
class ValidatedSeedWriter(SeedWriter):
    """Validate data before writing."""
    
    def write_seed(self, df, seed_name, metadata_columns=None):
        # Validation
        if df.isnull().any().any():
            logger.warning(f"Seed {seed_name} contains null values")
        
        if len(df) == 0:
            raise ValueError(f"Cannot write empty seed: {seed_name}")
        
        # Proceed with write
        return super().write_seed(df, seed_name, metadata_columns)
```

## 🎉 Benefits Summary

### Code Quality
- ✅ Single Responsibility Principle (SeedWriter only writes seeds)
- ✅ Open/Closed Principle (easy to extend via subclassing)
- ✅ Dependency Inversion (main.py depends on SeedWriter abstraction)

### Maintainability
- ✅ CSV writing logic in one place
- ✅ Easy to modify (only change seed_writer.py)
- ✅ Easy to test (isolate seed writing from extraction)

### Reusability
- ✅ Use SeedWriter for other data sources (APIs, databases)
- ✅ Use in different projects
- ✅ Extend with custom behavior (compression, validation)

### Debugging
- ✅ Metadata files track every run
- ✅ Seed summary for monitoring
- ✅ Clear separation makes issues easier to isolate

**You now have a production-grade, modular ingestion pipeline! 🚀**
