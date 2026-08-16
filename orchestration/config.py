"""orchestration/config.py

Single Pydantic BaseSettings class for all pipeline configuration.
Reads from environment variables and optional .env file.

(2026-08) Rewritten for the lake-native architecture.

REMOVED
    enable_quack / quack_* -- Quack was an escape hatch from serving.db's
        single-writer file lock. Consumers now attach the lake directly and
        the file-swap dance is gone, so there is nothing left for it to solve.
        Revisit after DuckDB 2.0 if a network endpoint is ever wanted.
    duckdb_path -- pointed at serving.db.
    export_background / export_timeout_seconds -- the async export bus existed
        to run CSV and Parquet concurrently against serving.db. Two COPY
        statements do not need a background thread.

ADDED
    ducklake_catalog_path / parquet_path -- the SAME variables ingestion and
        sqlmesh/config.yaml read. One value, three consumers: this is what
        stopped ingestion writing to one lake while SQLMesh read another.
    publish_* -- what serving/publish.py writes.
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

from pathlib import Path
from typing import List, Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PipelineConfig(BaseSettings):
    """Centralised, validated configuration for the Dagster pipeline."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── SQLMesh ────────────────────────────────────────────────────────────
    sqlmesh_env: str = "dev"
    """SQLMesh environment: dev, prod, staging, etc."""

    sqlmesh_start_date: str = "2024-10-01"
    """Start date for incremental models. Must not post-date the earliest
    business date in the data, or those intervals are never built."""

    # ── DuckLake ───────────────────────────────────────────────────────────
    # Read from the same env vars as ingestion/config/landing.py and
    # sqlmesh/config.yaml. If these three ever disagree, ingestion writes to
    # one lake and SQLMesh reads another -- silently, because the raw.* views
    # simply find no tables.
    ducklake_catalog_path: str = "data/warehouse/catalog.ducklake"
    parquet_path: str = "data/warehouse/parquet"

    # ── Landing ────────────────────────────────────────────────────────────
    landing_skip_duplicate_files: bool = True
    """Skip a workbook whose sha256 is already ingested."""
    landing_evolve_schema: bool = True
    """Add columns a workbook gained; False raises instead."""

    # ── Publish (Power BI / Excel) ─────────────────────────────────────────
    enable_parquet_publish: bool = True
    """Publish marts and bi to Parquet for Power BI."""
    enable_csv_publish: bool = False
    """Publish to CSV for Excel."""
    publish_schemas: List[str] = ["marts", "bi"]
    """Power BI's semantic model reads marts; Excel usually reads bi."""
    parquet_compression: str = "zstd"
    csv_delimiter: str = ";"
    """Semicolon by default: French-locale Excel reads a comma as a decimal
    separator and will not split columns on it."""

    @field_validator("parquet_compression")
    @classmethod
    def _valid_codec(cls, v: str) -> str:
        allowed = {"snappy", "zstd", "gzip", "brotli", "lz4", "uncompressed"}
        if v not in allowed:
            raise ValueError(f"parquet_compression must be one of {sorted(allowed)}")
        return v

    @field_validator("publish_schemas")
    @classmethod
    def _valid_schemas(cls, v: List[str]) -> List[str]:
        allowed = {"marts", "bi"}
        bad = set(v) - allowed
        if bad:
            raise ValueError(f"publish_schemas may only contain {sorted(allowed)}; got {sorted(bad)}")
        return v

    @model_validator(mode="after")
    def _check_paths(self) -> "PipelineConfig":
        # Relative paths are resolved against the project root by
        # shared.env.resolve_path, exactly as ingestion does. Reject shell
        # syntax here rather than letting it become a directory named "$(pwd)".
        for name in ("ducklake_catalog_path", "parquet_path"):
            value = getattr(self, name)
            if "$" in value:
                raise ValueError(
                    f"{name}={value!r} contains shell syntax. A .env file is not "
                    f"a shell: $(pwd) and ${{VAR}} are literal text."
                )
        return self