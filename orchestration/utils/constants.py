"""
orchestration/utils/constants.py

Central path and environment constants shared across all Dagster assets.
All paths are resolved relative to the project root so the pipeline
works regardless of where it is launched from.

Changes vs. previous version
──────────────────────────────
* get_serving_db_path(env) replaces the single SERVING_DB_PATH constant.
  The new serving layer uses env-named files:
      dev   → data/warehouse/serving_dev.db
      prod  → data/warehouse/serving.db   (unchanged for production)
* QUACK_HOST and QUACK_PORT defaults added for Quack mode.
* CSV_EXPORTS_DIR and PARQUET_EXPORTS_DIR now point to the new
  env-namespaced layout:  data/exports/csv/<env>/
                          data/exports/parquet/<env>/
* QUALITY_REPORTS_DIR kept as-is (not affected by serving changes).
"""

from pathlib import Path

from shared.paths import (
    ARCHIVE_DIR,
    DATA_DIR,
    INPUT_PATHS,
    PROJECT_ROOT,
    SQLMESH_SEEDS_DIR,
    WAREHOUSE_DIR,
)


# ── Project root (root package anchor) ─────────────────────────────────────
# Shared path definitions are centralized in shared.paths so ingestion and
# orchestration stay aligned on the filesystem layout.


# ── Sub-project roots ──────────────────────────────────────────────────────
INGESTION_ROOT = PROJECT_ROOT / "ingestion"
SQLMESH_ROOT   = PROJECT_ROOT / "sqlmesh"
SERVING_ROOT   = PROJECT_ROOT / "serving"
DATA_ROOT      = DATA_DIR


# ── Input & source directories ─────────────────────────────────────────────
SOURCE_PATHS = {
    "sales":      DATA_ROOT / "source" / "sales",
    "targets":    DATA_ROOT / "source" / "targets",
    "references": DATA_ROOT / "source" / "references",
    "kp_sd":      DATA_ROOT / "source" / "kp_sd",
}

# Shared input paths derived from the canonical project layout.


# ── SQLMesh paths ──────────────────────────────────────────────────────────
SEEDS_DIR     = SQLMESH_SEEDS_DIR
SQLMESH_STATE = SQLMESH_ROOT / "sqlmesh_state.db"


# ── Warehouse ──────────────────────────────────────────────────────────────
DUCKLAKE_PATH        = WAREHOUSE_DIR / "catalog.ducklake"
DUCKLAKE_CONN_STRING = f"ducklake:{DUCKLAKE_PATH}"
PARQUET_STORAGE_DIR  = WAREHOUSE_DIR / "parquet_storage"


# ── Serving DB — env-aware ─────────────────────────────────────────────────
# The new serving layer writes:
#   dev  → serving_dev.db   (safe to wipe / swap during development)
#   prod → serving.db       (stable path BI tools are hardcoded to)
#
# Always use get_serving_db_path(env) instead of a bare constant so assets
# and resources never disagree about the file they point at.

def get_serving_db_path(env: str = "dev") -> Path:
    """Return the absolute path to the serving DuckDB for *env*."""
    filename = "serving.db" if env == "prod" else f"serving_{env}.db"
    return WAREHOUSE_DIR / filename


# Convenience alias kept for backward compatibility with any code that
# imported the old SERVING_DB_PATH.  Points to the dev file by default;
# update callers to use get_serving_db_path() for full env-awareness.
SERVING_DB_PATH = get_serving_db_path("dev")


# ── Export directories (env-namespaced subdirs created at runtime) ─────────
EXPORTS_DIR         = DATA_ROOT / "exports"
CSV_EXPORTS_DIR     = EXPORTS_DIR / "csv"       # subdir /<env>/ added at runtime
PARQUET_EXPORTS_DIR = EXPORTS_DIR / "parquet"   # subdir /<env>/ added at runtime
QUALITY_REPORTS_DIR = EXPORTS_DIR / "quality_reports"


# ── Archive ────────────────────────────────────────────────────────────────


# ── Dead-letter queue (failed file processing) ─────────────────────────────
DEAD_LETTER_DIR = DATA_ROOT / "dead_letter"


# ── Pipeline state ─────────────────────────────────────────────────────────
STATE_FILE = DATA_ROOT / ".processed_files_state.json"


# ── DuckLake / catalog ─────────────────────────────────────────────────────
CATALOG_NAME = "sales_lakehouse"


# ── SQLMesh environment ────────────────────────────────────────────────────
# Switch to "prod" for production runs.
SQLMESH_ENV = "dev"


# ── Quack defaults ─────────────────────────────────────────────────────────
# Override at runtime via QUACK_HOST / QUACK_PORT / QUACK_TOKEN env-vars.
QUACK_HOST = "localhost"
QUACK_PORT = 9494 

# ── Processing config ──────────────────────────────────────────────────────
SALES_SHEET_FILTER = "Synthese *"   # Sheet pattern to delete during preprocessing
BATCH_TIMEOUT      = 3600           # Max seconds per pipeline run (1 hour)