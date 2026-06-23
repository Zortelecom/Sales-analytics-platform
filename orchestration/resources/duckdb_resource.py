"""
orchestration/resources/duckdb_resource.py

Changes vs. previous version
──────────────────────────────
* DuckDBResource.get_connection() default path now delegates to
  get_serving_db_path() so the resource and the serving asset always
  resolve the same env-aware filename (serving_dev.db / serving.db).
* QuackResource added: wraps a DuckDB connection that ATTACHes to a
  running Quack server instead of opening a local file.  Used by assets
  that need to query the live serving DB in Quack mode (e.g. a future
  live-validation asset).  Not wired into definitions.py by default —
  callers instantiate it directly or register it when ENABLE_QUACK=true.
* DuckLakeResource unchanged.
* All env-var reads centralised via PipelineConfig from orchestration/config.
"""

from __future__ import annotations

from typing import Optional
import logging
import duckdb
from dagster import ConfigurableResource
from pydantic import Field

from orchestration.config import PipelineConfig
from orchestration.utils.constants import (
    QUACK_HOST,
    QUACK_PORT,
    get_serving_db_path,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

_cfg = PipelineConfig()


class DuckDBResource(ConfigurableResource):
    """
    DuckDB connection resource.

    database_path defaults to the env-aware serving DB path so nothing
    breaks when operators switch between dev and prod environments.
    """

    database_path: Optional[str] = Field(
        default=None,
        description=(
            "Path to DuckDB database file.  "
            "Defaults to get_serving_db_path(SQLMESH_ENV) when not set."
        ),
    )
    read_only: bool = Field(
        default=False,
        description="Connect in read-only mode.",
    )

    def _resolved_path(self) -> str:
        if self.database_path:
            return self.database_path
        return str(get_serving_db_path(_cfg.sqlmesh_env))

    def get_connection(self) -> duckdb.DuckDBPyConnection:
        """Return a new DuckDB connection to the configured database."""
        return duckdb.connect(self._resolved_path(), read_only=self.read_only)

    def query(self, sql: str):
        """Execute *sql* and return results as a DataFrame."""
        conn = self.get_connection()
        try:
            return conn.execute(sql).fetchdf()
        finally:
            conn.close()


class DuckLakeResource(ConfigurableResource):
    """DuckLake catalog resource."""

    catalog_path: str = Field(
        default="data/warehouse/catalog.ducklake",
        description="Path to DuckLake catalog.",
    )

    def get_connection(self) -> duckdb.DuckDBPyConnection:
        """Return a DuckDB connection with the DuckLake catalog attached."""
        conn = duckdb.connect()
        conn.execute(f"ATTACH '{self.catalog_path}' AS sales_lakehouse")
        conn.execute("USE sales_lakehouse")
        return conn


class QuackResource(ConfigurableResource):
    """
    DuckDB connection that talks to a running Quack server.

    Use this resource (or instantiate it directly in an asset) when
    ENABLE_QUACK=true and you need to query the live serving DB from
    a Dagster asset without touching the local file.

    The connection ATTACHes to the Quack server as a named catalog
    ("serving") so queries look like:
        SELECT * FROM serving.bi.fact_sales LIMIT 10
    """

    host: str = Field(default=QUACK_HOST, description="Quack server hostname.")
    port: int = Field(default=QUACK_PORT, description="Quack server port.")
    token: str = Field(default="", description="Quack authentication token.")
    catalog_alias: str = Field(
        default="serving",
        description="Local alias for the ATTACHed Quack catalog.",
    )

    def get_connection(self) -> duckdb.DuckDBPyConnection:
        """
        Return an in-memory DuckDB connection ATTACHed to the Quack server.

        Callers are responsible for closing the connection.
        """
        if not self.token:
            raise ValueError(
                "QuackResource: token must be set (QUACK_TOKEN env-var or "
                "resource config)."
            )
        conn = duckdb.connect()
        conn.execute("LOAD quack")
        conn.execute(
            f"CREATE SECRET IF NOT EXISTS _quack_secret "
            f"(TYPE quack, TOKEN '{self.token}')"
        )
        conn.execute(
            f"ATTACH 'quack:{self.host}:{self.port}' AS {self.catalog_alias}"
        )
        logger.warning('''Quack is in beta (DuckDB ≥ v1.5.2). Stable release: 
            DuckDB v2.0 Sep 2026. Use file-swap in production.''')
        return conn

    def query(self, sql: str):
        """Execute *sql* via the Quack connection and return a DataFrame."""
        conn = self.get_connection()
        try:
            return conn.execute(sql).fetchdf()
        finally:
            conn.close()
