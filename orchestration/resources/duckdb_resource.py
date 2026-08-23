"""
orchestration/resources/duckdb_resource.py

(2026-08) Rewritten for the lake-native architecture.


FIXED IN DuckLakeResource
    The ATTACH was `ATTACH '{path}' AS sales_lakehouse` -- no `ducklake:`
    prefix and no INSTALL/LOAD. DuckDB would have opened the catalog file as a
    plain database, so every schema would appear empty rather than raising:
    the pipeline would report success and validate nothing. It also never
    passed READ_ONLY, so a validation asset could take a write lock on the
    catalog the pipeline was about to write to.

    Every read path connected as the WRITING role. shared/lake.py resolves a
    per-role credential (PG_CATALOG_USER_READER / _PUBLISHER / _WRITER, falling
    back to PG_CATALOG_USER), and serving/publish.py already asks for
    role="publisher" -- but this resource passed role=None whenever read_only
    was True, so marts_validation, pipeline_health_sensor and
    data_quality_checks all attached the PostgreSQL catalog as lake_writer.

    That made `ingestion.main --check`'s warning about PG_CATALOG_USER_READER
    misleading: it told you least privilege was not in force because the
    variable was unset, when in fact nothing asked for that role even when it
    WAS set. Least privilege is not a credential you configure, it is a
    credential you request.
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

from shared import lake
from shared.env import load_env, resolve_path
from shared.paths import WAREHOUSE_DIR

load_env()
logger = logging.getLogger(__name__)

CATALOG_ALIAS = lake.CATALOG_ALIAS


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
    role: Optional[str] = Field(
        default=None,
        description=(
            "Override the credential role. Normally leave unset: read_only "
            "selects 'reader' and a write attach selects 'writer'. Set it only "
            "when a caller needs a credential that does not follow from its "
            "read/write intent."
        ),
    )

    def _path(self):
        return resolve_path(
            self.catalog_path or os.getenv("DUCKLAKE_CATALOG_PATH"),
            WAREHOUSE_DIR / "catalog.ducklake",
        )

    def _role(self) -> str:
        """
        The credential role this attach should use.

        Derived from intent rather than passed in, so a read-only caller cannot
        quietly hold a writing credential. shared/lake.py falls back to
        PG_CATALOG_USER when the per-role variable is unset, and on the DuckDB
        file backend the role is ignored entirely -- so this is safe to set
        unconditionally on both backends.
        """
        return self.role or ("reader" if self.read_only else "writer")

    def get_connection(self) -> duckdb.DuckDBPyConnection:
        """
        Attach the lake via shared/lake.py.

        The ATTACH used to be built here, and this copy was the one that
        omitted the `ducklake:` prefix -- so DuckDB opened the catalog as an
        ordinary database file and every schema looked EMPTY rather than
        raising. Four copies of one statement is how that survives review.
        """
        if not lake.is_postgres_catalog():
            path = self._path()
            if not path.exists():
                raise FileNotFoundError(
                    f"DuckLake catalog not found at {path}. Check "
                    f"DUCKLAKE_CATALOG_PATH and that ingestion has run."
                )

        role = self._role()
        logger.debug("Attaching lake as role=%s (%s)", role, lake.describe(role))
        return lake.connect(
            read_only=self.read_only,
            role=role,
            alias=CATALOG_ALIAS,
        )

    def query(self, sql: str, params: list | None = None):
        conn = self.get_connection()
        try:
            return conn.execute(sql, params or []).fetchdf()
        finally:
            conn.close()