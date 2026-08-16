"""
orchestration/utils/constants.py

Central path constants shared across Dagster assets. Everything resolves from
the project root so the pipeline works regardless of where it is launched.

(2026-08) Cleaned for the lake-native architecture.

REMOVED
    get_serving_db_path / SERVING_DB_PATH -- serving.db is gone. Consumers
        attach the lake; Power BI and Excel read published files.
    SEEDS_DIR -- sqlmesh/seeds/ is gone; ingestion writes to landing.
    QUACK_HOST / QUACK_PORT -- see orchestration/config.py.
    SOURCE_PATHS -- a THIRD copy of the source directory map, alongside
        sources.yaml and settings.py, and it disagreed with both: it claimed
        data/source/<type> while sources.yaml points at the synced folder.
        Nothing read it. Use shared.sources.load_sources().
    INPUT_PATHS import -- now a deprecation shim in shared.paths that warns.
    SALES_SHEET_FILTER -- a fourth place the "Synthese *" pattern lived, with
        the trailing-space variant that misses "SyntheseJan". sources.yaml
        owns it.
"""

from pathlib import Path

from shared.paths import (
    ARCHIVE_DIR,
    DATA_DIR,
    DEAD_LETTER_DIR,
    LOGS_DIR,
    PROJECT_ROOT,
    WAREHOUSE_DIR,
)

# ── Sub-project roots ──────────────────────────────────────────────────────
INGESTION_ROOT = PROJECT_ROOT / "ingestion"
SQLMESH_ROOT = PROJECT_ROOT / "sqlmesh"
SERVING_ROOT = PROJECT_ROOT / "serving"
DATA_ROOT = DATA_DIR

# ── Warehouse ──────────────────────────────────────────────────────────────
# Defaults only. PipelineConfig reads DUCKLAKE_CATALOG_PATH / PARQUET_PATH,
# which is what assets should use -- these are for code with no config in hand.
DUCKLAKE_PATH = WAREHOUSE_DIR / "catalog.ducklake"
DUCKLAKE_CONN_STRING = f"ducklake:{DUCKLAKE_PATH}"
PARQUET_STORAGE_DIR = WAREHOUSE_DIR / "parquet"
CATALOG_NAME = "sales_lakehouse"

# ── Exports ────────────────────────────────────────────────────────────────
EXPORTS_DIR = DATA_ROOT / "exports"
QUALITY_REPORTS_DIR = EXPORTS_DIR / "quality_reports"

# ── Pipeline state ─────────────────────────────────────────────────────────
STATE_FILE = DATA_ROOT / ".processed_files_state.json"

BATCH_TIMEOUT = 3600


def schema_for(logical: str, env: str = "dev") -> str:
    """
    Physical schema name for a logical one.

    SQLMesh namespaces non-prod environments: marts__dev, bi__dev, meta__dev;
    prod keeps the bare name. Every asset that builds a schema name should call
    this rather than inlining the f-string -- that pattern was already repeated
    in three assets with two different spellings.
    """
    return logical if env == "prod" else f"{logical}__{env}"