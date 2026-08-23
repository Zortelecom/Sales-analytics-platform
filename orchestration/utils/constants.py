"""
orchestration/utils/constants.py

Central path constants shared across Dagster assets. Everything resolves from
the project root so the pipeline works regardless of where it is launched.

"""

from shared import lake
from shared.paths import (
    DATA_DIR,
    DEAD_LETTER_DIR,
    PROJECT_ROOT,
    WAREHOUSE_DIR,
)

# ── Sub-project roots ──────────────────────────────────────────────────────
INGESTION_ROOT = PROJECT_ROOT / "ingestion"
SQLMESH_ROOT = PROJECT_ROOT / "sqlmesh"
SERVING_ROOT = PROJECT_ROOT / "serving"
DATA_ROOT = DATA_DIR

# ── Warehouse ──────────────────────────────────────────────────────────────
# CATALOG_NAME is the ATTACH alias, and it is LOAD-BEARING: it appears in every
# SQLMesh-generated view definition and in every `database_name = ?` filter in
# marts_validation and publish.py. It must match shared/lake.py,
# ingestion/config/landing.py and sqlmesh/config.yaml.
#
# It used to be the literal "sales_lakehouse" here -- a fourth hardcoded copy,
# while duckdb_resource.py and serving/publish.py both derive theirs from
# lake.CATALOG_ALIAS. A drift would have surfaced as
#     Schema 'marts__dev' not found. Present: []
# from marts_validation, which reads as a wrong SQLMESH_ENV and sends you
# looking in the wrong file entirely.
CATALOG_NAME = lake.CATALOG_ALIAS

# Default only, and only meaningful on the DuckDB FILE backend. Under a
# PostgreSQL catalog this path names a file no process opens -- do not use it
# to report provenance. lake.describe(role) answers "where did this come
# from" on both backends.
DUCKLAKE_PATH = WAREHOUSE_DIR / "catalog.ducklake"
PARQUET_STORAGE_DIR = WAREHOUSE_DIR / "parquet"

# ── Exports ────────────────────────────────────────────────────────────────
EXPORTS_DIR = DATA_ROOT / "exports"
QUALITY_REPORTS_DIR = EXPORTS_DIR / "quality_reports"

# ── Pipeline state ─────────────────────────────────────────────────────────
STATE_FILE = DATA_ROOT / ".processed_files_state.json"

BATCH_TIMEOUT = 3600

# Re-exported: preprocessing.py and file_discovery.py import it from here.
__all__ = [
    "BATCH_TIMEOUT",
    "CATALOG_NAME",
    "DATA_ROOT",
    "DEAD_LETTER_DIR",
    "DUCKLAKE_PATH",
    "EXPORTS_DIR",
    "INGESTION_ROOT",
    "PARQUET_STORAGE_DIR",
    "QUALITY_REPORTS_DIR",
    "SERVING_ROOT",
    "SQLMESH_ROOT",
    "STATE_FILE",
    "schema_for",
]


def schema_for(logical: str, env: str = "dev") -> str:
    """
    Physical schema name for a logical one.

    SQLMesh namespaces non-prod environments: marts__dev, bi__dev, meta__dev;
    prod keeps the bare name. Every asset that builds a schema name should call
    this rather than inlining the f-string -- that pattern was already repeated
    in three assets with two different spellings.
    """
    return logical if env == "prod" else f"{logical}__{env}"