"""Constants and configuration for Dagster pipeline"""
from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
INGESTION_ROOT = PROJECT_ROOT / "ingestion"
SQLMESH_ROOT = PROJECT_ROOT / "sqlmesh"
SERVING_ROOT = PROJECT_ROOT / "serving"
DATA_ROOT = PROJECT_ROOT / "data"

# Data paths
SOURCE_PATHS = {
    "sales": DATA_ROOT / "source" / "sales",
    "targets": DATA_ROOT / "source" / "targets",
    "references": DATA_ROOT / "source" / "references",
}

INPUT_PATHS = {
    "sales": DATA_ROOT / "input" / "sales",
    "targets": DATA_ROOT / "input" / "targets",
    "references": DATA_ROOT / "input" / "references",
}

SEEDS_PATH = SQLMESH_ROOT / "seeds"
ARCHIVE_PATH = DATA_ROOT / "archive"
WAREHOUSE_PATH = DATA_ROOT / "warehouse"

DUCKLAKE_PATH = WAREHOUSE_PATH / "catalog.ducklake"
DUCKLAKE_CONN_STRING = f"ducklake:{DUCKLAKE_PATH}"
SERVING_DB_PATH = WAREHOUSE_PATH / "serving.db"

# Processing config
SALES_SHEET_FILTER = "Synthese *"  # Sheets to delete
BATCH_TIMEOUT = 3600  # 1 hour max per run

# State file
STATE_FILE = PROJECT_ROOT / "data" / ".processed_files_state.json"

CATALOG_NAME = "sales_lakehouse" 