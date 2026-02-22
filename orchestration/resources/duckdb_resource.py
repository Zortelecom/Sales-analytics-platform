"""DuckDB resource for Dagster"""
import duckdb
from dagster import ConfigurableResource
from pydantic import Field
from typing import Optional


class DuckDBResource(ConfigurableResource):
    """DuckDB connection resource"""
    database_path: Optional[str] = Field(
        default=None,
        description="Path to DuckDB database file"
    )
    read_only: bool = Field(
        default=False,
        description="Connect in read-only mode"
    )

    def get_connection(self):
        """Get DuckDB connection"""
        if self.database_path:
            return duckdb.connect(self.database_path, read_only=self.read_only)
        return duckdb.connect()  # In-memory

    def query(self, sql: str):
        """Execute query and return results"""
        conn = self.get_connection()
        try:
            return conn.execute(sql).fetchdf()
        finally:
            conn.close()


class DuckLakeResource(ConfigurableResource):
    """DuckLake catalog resource"""
    catalog_path: str = Field(
        default="data/warehouse/catalog.ducklake",
        description="Path to DuckLake catalog"
    )

    def get_connection(self):
        """Get DuckDB connection with DuckLake attached"""
        conn = duckdb.connect()
        conn.execute(f"ATTACH '{self.catalog_path}' AS sales_lakehouse")
        conn.execute("USE sales_lakehouse")
        return conn
