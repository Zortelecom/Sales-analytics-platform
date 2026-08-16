"""
Central configuration for the ingestion pipeline: paths and DB connection
strings.

(2026-08 review)
────────────────
sources.yaml is now parsed only by shared/sources.py. SourceConfig no longer
declares one Path field per source -- that hand-maintained list was the reason
the two KP-SD sources went missing from it, and the reason the previous
docstring claimed kp_sd_destocke_path / kp_sd_non_destocke_path existed when
the dataclass only ever had kp_sd_path.

SourceConfig now wraps the parsed registry. The four named properties are
back-compat shims for FileDiscovery; new code should index source_paths /
input_paths by source_type, which requires no edit here when a source is added.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from shared.paths import (
    ARCHIVE_DIR,
    DATA_DIR,
    PROJECT_ROOT,
    SQLMESH_SEEDS_DIR,
    WAREHOUSE_DIR,
)
from shared.sources import SourceSpec, load_sources

# ── DuckLake / serving ─────────────────────────────────────────────────────
COMPUTE_DB_PATH = WAREHOUSE_DIR / "ducklake.db"
PARQUET_STORAGE_PATH = WAREHOUSE_DIR / "parquet"

_CATALOG_FILE = WAREHOUSE_DIR / "catalog.ducklake"
CATALOG_CONNECTION_STRING = f"ducklake:{_CATALOG_FILE}"

# Multi-client example (uncomment to switch). A DuckDB-file catalog limits the
# lake to a single client; Postgres is the supported multi-client backend.
# import os
# CATALOG_CONNECTION_STRING = os.getenv(
#     "DUCKLAKE_CATALOG_CONN",
#     "ducklake:postgres:dbname=ducklake_catalog host=localhost user=postgres",
# )


# ── Source configuration ───────────────────────────────────────────────────

@dataclass(frozen=True)
class SourceConfig:
    source_paths: Dict[str, Path]      # source_type -> synced source folder
    input_paths: Dict[str, Path]       # source_type -> data/input/<subdir>
    processing_rules: Dict[str, dict]  # source_type -> {delete_sheets_pattern, required_sheets}

    # Back-compat shims. Prefer source_paths[source_type].
    @property
    def sales_path(self) -> Path:
        return self.source_paths["sales"]

    @property
    def targets_path(self) -> Path:
        return self.source_paths["targets"]

    @property
    def references_path(self) -> Path:
        return self.source_paths["references"]

    @property
    def kp_sd_path(self) -> Path:
        return self.source_paths["kp_sd"]


def load_sources_config() -> SourceConfig:
    """Resolved source configuration. Thin adapter over shared.sources."""
    registry = load_sources()
    return SourceConfig(
        source_paths=registry.source_paths,
        input_paths=registry.input_paths,
        processing_rules={
            source_type: {**spec.processing.model_dump(),
                          "file_pattern": spec.file_pattern}
            for source_type, spec in registry.sources.items()
        },
    )


def get_source(source_type: str) -> SourceSpec:
    """Single-source lookup with a helpful error on unknown source_type."""
    return load_sources()[source_type]


# INPUT_PATHS is re-exported for call sites that do
# `from ingestion.config.settings import INPUT_PATHS`. Prefer
# `load_sources().input_paths`.
INPUT_PATHS = load_sources().input_paths

# Old code that imported ARCHIVE_PATH still works; prefer ARCHIVE_DIR.
ARCHIVE_PATH = ARCHIVE_DIR