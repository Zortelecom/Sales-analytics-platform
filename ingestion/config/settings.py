"""
Central configuration for the ingestion pipeline.
Handles paths and database connection strings.

Fix applied
───────────
INPUT_PATHS and ARCHIVE_PATH previously used bare relative Path() literals
(e.g. Path("data/input/sales")).  Running the pipeline from any directory
other than the project root would silently write seeds and archives to the
wrong location.  Both are now derived from PROJECT_ROOT, which is resolved
absolutely at import time from this file's own location, so the pipeline
is launch-directory-agnostic.
"""
from pathlib import Path
from dataclasses import dataclass
from typing import Dict
import yaml

from shared.paths import (
    ARCHIVE_DIR,
    DATA_DIR,
    INPUT_PATHS,
    PROJECT_ROOT,
    SQLMESH_SEEDS_DIR,
    WAREHOUSE_DIR,
)

# ── DuckLake / serving ─────────────────────────────────────────────────────
COMPUTE_DB_PATH      = WAREHOUSE_DIR / "ducklake.db"
PARQUET_STORAGE_PATH = WAREHOUSE_DIR / "parquet_storage"

_CATALOG_FILE            = WAREHOUSE_DIR / "catalog.ducklake"
CATALOG_CONNECTION_STRING = f"ducklake:{_CATALOG_FILE}"

# Production example (uncomment to switch):
# import os
# CATALOG_CONNECTION_STRING = os.getenv(
#     "DUCKLAKE_CATALOG_CONN",
#     "ducklake:postgres:dbname=ducklake_catalog host=localhost user=postgres password=password"
# )


# ── Source configuration ───────────────────────────────────────────────────

@dataclass
class SourceConfig:
    sales_path:       Path
    targets_path:     Path
    references_path:  Path
    processing_rules: Dict


def load_sources_config() -> SourceConfig:
    """Load and resolve source configuration from sources.yaml."""
    config_path = Path(__file__).parent / "sources.yaml"

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if config["sources"]["local_sync"]["enabled"]:
        base    = Path(config["sources"]["local_sync"]["base_path"]).expanduser()
        folders = config["sources"]["local_sync"]["folders"]
    else:
        raise NotImplementedError("SharePoint API mode not yet implemented")

    return SourceConfig(
        sales_path=base       / folders["sales"],
        targets_path=base     / folders["targets"],
        references_path=base  / folders["references"],
        processing_rules=config["processing"],
    )


# ── Backward-compatibility alias ───────────────────────────────────────────
# Old code that imported ARCHIVE_PATH still works; prefer ARCHIVE_DIR.
ARCHIVE_PATH = ARCHIVE_DIR