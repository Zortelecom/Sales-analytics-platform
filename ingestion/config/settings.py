"""
Central configuration for the ingestion pipeline: paths and DB connection
strings.
SourceConfig  wraps the parsed registry. The four named properties are
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
    WAREHOUSE_DIR,
)
from shared.sources import SourceSpec, load_sources

# ── DuckLake / serving ─────────────────────────────────────────────────────
COMPUTE_DB_PATH = WAREHOUSE_DIR / "ducklake.db"
PARQUET_STORAGE_PATH = WAREHOUSE_DIR / "parquet"

_CATALOG_FILE = WAREHOUSE_DIR / "catalog.ducklake"
CATALOG_CONNECTION_STRING = f"ducklake:{_CATALOG_FILE}"


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