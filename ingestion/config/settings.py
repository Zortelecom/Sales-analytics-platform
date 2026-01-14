"""
Central configuration for the ingestion pipeline.
Handles paths and database connection strings.
"""

from pathlib import Path

# --- Project Paths ---
# resolving from ingestion/config/config.py -> ingestion/config -> ingestion -> root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"

INPUT_SALES_DIR = DATA_DIR / "input" / "sales"
INPUT_TARGETS_DIR = DATA_DIR / "input" / "targets"
INPUT_REFERENCES_DIR = DATA_DIR / "input" / "references"
ARCHIVE_DIR = DATA_DIR / "archive"
WAREHOUSE_DIR = DATA_DIR / "warehouse"
SQLMESH_DIR = PROJECT_ROOT / "sqlmesh"
SQLMESH_SEEDS_DIR = SQLMESH_DIR / "seeds"

# --- DuckLake Configuration ---
# The local DuckDB instance that performs the compute
COMPUTE_DB_PATH = WAREHOUSE_DIR / "ducklake.db"

# The physical storage for Parquet files
PARQUET_STORAGE_PATH = WAREHOUSE_DIR / "parquet_storage"

# --- CATALOG CONFIGURATION (The Switch) ---
# CURRENT MODE: Local DuckDB File Catalog
# FUTURE MODE: PostgreSQL -> "ducklake:postgres:dbname=catalog host=localhost user=admin"

# We construct the connection string dynamically.
# If using a local file, the syntax is 'ducklake:path/to/file'
_CATALOG_FILE = WAREHOUSE_DIR / "catalog.ducklake"
CATALOG_CONNECTION_STRING = f"ducklake:{_CATALOG_FILE}"

# Example for Production (Uncomment to switch):
# CATALOG_CONNECTION_STRING = os.getenv(
#     "DUCKLAKE_CATALOG_CONN",
#     "ducklake:postgres:dbname=ducklake_catalog host=localhost user=postgres password=password"
# )
