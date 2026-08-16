"""Resource exports. DuckDBResource and QuackResource were removed with serving.db."""
from orchestration.resources.duckdb_resource import DuckLakeResource
from orchestration.resources.sqlmesh_resource import SQLMeshResource

__all__ = ["DuckLakeResource", "SQLMeshResource"]
