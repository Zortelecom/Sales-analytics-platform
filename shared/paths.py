"""
Filesystem constants. Launch-directory-agnostic: everything derives from
PROJECT_ROOT, resolved from this file's own location at import time.

INPUT_PATHS moved to shared/sources.py, where it is derived from
sources.yaml rather than hand-maintained. A deprecation shim below keeps
existing `from shared.paths import INPUT_PATHS` call sites working; remove
it once the extractors and orchestration assets have been migrated.
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SQLMESH_DIR = PROJECT_ROOT / "sqlmesh"
SQLMESH_SEEDS_DIR = SQLMESH_DIR / "seeds"
WAREHOUSE_DIR = DATA_DIR / "warehouse"
ARCHIVE_DIR = DATA_DIR / "archive"

DATA_ROOT = DATA_DIR


def __getattr__(name: str) -> Any:
    """
    PEP 562 lazy attribute. The deferred import of shared.sources breaks the
    cycle (shared.sources imports DATA_DIR from this module at import time).
    """
    if name == "INPUT_PATHS":
        from shared.sources import load_sources

        warnings.warn(
            "shared.paths.INPUT_PATHS is deprecated and will be removed. "
            "Use shared.sources.load_sources().input_paths, or "
            "load_sources()[source_type].input_dir for a single source.",
            DeprecationWarning,
            stacklevel=2,
        )
        return load_sources().input_paths

    if name in {
        "INPUT_SALES_DIR",
        "INPUT_TARGETS_DIR",
        "INPUT_REFERENCES_DIR",
        "INPUT_KP_SD_DIR",
    }:
        from shared.sources import load_sources

        source_type = name.removeprefix("INPUT_").removesuffix("_DIR").lower()
        warnings.warn(
            f"shared.paths.{name} is deprecated. "
            f"Use load_sources()[{source_type!r}].input_dir.",
            DeprecationWarning,
            stacklevel=2,
        )
        return load_sources()[source_type].input_dir

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")