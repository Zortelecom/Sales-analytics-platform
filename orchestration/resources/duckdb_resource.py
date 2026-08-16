"""
orchestration/resources/duckdb_resource.py

(2026-08) Rewritten for the lake-native architecture.

REMOVED
    DuckDBResource -- it opened serving.db, which no longer exists.
    QuackResource -- Quack existed to work around serving.db's single-writer
        file lock. Consumers attach the lake directly now.

FIXED IN DuckLakeResource
    The ATTACH was `ATTACH '{path}' AS sales_lakehouse` -- no `ducklake:`
    prefix and no INSTALL/LOAD. DuckDB would have opened the catalog file as a
    plain database, so every schema would appear empty rather than raising:
    the pipeline would report success and validate nothing. It also never
    passed READ_ONLY, so a validation asset could take a write lock on the
    catalog the pipeline was about to write to.
"""

# NOTE: deliberately NO `from __future__ import annotations`.
#
# PEP 563 turns every annotation into a string, and Dagster resolves the
# `context` parameter by inspecting the actual class:
#
#   DagsterInvalidDefinitionError: Cannot annotate `context` parameter with
#   type AssetExecutionContext
#
# ...which reads as though the annotation is wrong when the annotation is the
# only correct one. Python 3.10+ handles `str | None` and `dict[str, X]`
# natively, so the import buys nothing here.

import logging
import os
from typing import Optional

import duckdb
from dagster import ConfigurableResource
from pydantic import Field

from shared.env import load_env, resolve_path
from shared.paths import WAREHOUSE_DIR

load_env()
logger = logging.getLogger(__name__)

CATALOG_ALIAS = "sales_lakehouse"


class DuckLakeResource(ConfigurableResource):
    """A DuckDB connection with the DuckLake catalog attached."""

    catalog_path: Optional[str] = Field(
        default=None,
        description="DuckLake catalog path. Defaults to DUCKLAKE_CATALOG_PATH.",
    )
    read_only: bool = Field(
        default=True,
        description=(
            "Attach read-only. Default True: validation and quality assets have "
            "no business writing, and a write attach would lock the catalog "
            "against SQLMesh."
        ),
    )

    def _path(self):
        return resolve_path(
            self.catalog_path or os.getenv("DUCKLAKE_CATALOG_PATH"),
            WAREHOUSE_DIR / "catalog.ducklake",
        )

    def get_connection(self) -> duckdb.DuckDBPyConnection:
        path = self._path()
        if not path.exists():
            raise FileNotFoundError(
                f"DuckLake catalog not found at {path}. Check "
                f"DUCKLAKE_CATALOG_PATH and that ingestion has run."
            )

        conn = duckdb.connect()
        conn.execute("INSTALL ducklake; LOAD ducklake;")
        options = " (READ_ONLY)" if self.read_only else ""
        # The 'ducklake:' prefix is required. Without it DuckDB opens the
        # catalog as an ordinary database file and every schema looks empty.
        conn.execute(f"ATTACH 'ducklake:{path.as_posix()}' AS {CATALOG_ALIAS}{options}")
        conn.execute(f"USE {CATALOG_ALIAS}")
        return conn

    def query(self, sql: str, params: list | None = None):
        conn = self.get_connection()
        try:
            return conn.execute(sql, params or []).fetchdf()
        finally:
            conn.close()