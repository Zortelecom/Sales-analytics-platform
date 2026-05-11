"""
orchestration/utils/constants.py

Central path and environment constants shared across all Dagster assets.
All paths are resolved relative to the project root so the pipeline
works regardless of where it is launched from.
"""

from pathlib import Path

# ── Project root (three levels up from this file) ─────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()


# ── Sub-project roots ──────────────────────────────────────────────────────
INGESTION_ROOT = PROJECT_ROOT / "ingestion"
SQLMESH_ROOT   = PROJECT_ROOT / "sqlmesh"
SERVING_ROOT   = PROJECT_ROOT / "serving"
DATA_ROOT      = PROJECT_ROOT / "data"


# ── Input & source directories ─────────────────────────────────────────────
SOURCE_PATHS = {
    "sales":      DATA_ROOT / "source" / "sales",
    "targets":    DATA_ROOT / "source" / "targets",
    "references": DATA_ROOT / "source" / "references",
}

INPUT_PATHS = {
    "sales":      DATA_ROOT / "input" / "sales",
    "targets":    DATA_ROOT / "input" / "targets",
    "references": DATA_ROOT / "input" / "references",
}


# ── SQLMesh paths ──────────────────────────────────────────────────────────
SEEDS_DIR     = SQLMESH_ROOT / "seeds"
SQLMESH_STATE = DATA_ROOT / "sqlmesh_state.db"


# ── Warehouse & serving ────────────────────────────────────────────────────
WAREHOUSE_DIR        = DATA_ROOT / "warehouse"
DUCKLAKE_PATH        = WAREHOUSE_DIR / "catalog.ducklake"
DUCKLAKE_CONN_STRING = f"ducklake:{DUCKLAKE_PATH}"
SERVING_DB_PATH      = WAREHOUSE_DIR / "serving.db"
PARQUET_STORAGE_DIR  = WAREHOUSE_DIR / "parquet_storage"


# ── Export & quality report directories ───────────────────────────────────
EXPORTS_DIR         = DATA_ROOT / "exports"
CSV_EXPORTS_DIR     = EXPORTS_DIR / "csv"
PARQUET_EXPORTS_DIR = EXPORTS_DIR / "parquet"
QUALITY_REPORTS_DIR = EXPORTS_DIR / "quality_reports"


# ── Archive ────────────────────────────────────────────────────────────────
ARCHIVE_DIR = DATA_ROOT / "archive"


# ── Pipeline state ─────────────────────────────────────────────────────────
STATE_FILE = DATA_ROOT / ".processed_files_state.json"


# ── DuckLake / catalog ─────────────────────────────────────────────────────
CATALOG_NAME = "sales_lakehouse"


# ── SQLMesh environment ────────────────────────────────────────────────────
# Switch to "prod" for production runs.
SQLMESH_ENV = "dev"


# ── Processing config ──────────────────────────────────────────────────────
SALES_SHEET_FILTER = "Synthese *"   # Sheet pattern to delete during preprocessing
BATCH_TIMEOUT      = 3600           # Max seconds per pipeline run (1 hour)
