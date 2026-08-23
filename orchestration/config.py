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

(2026-08b) `reports` IS NOW A FIRST-CLASS SERVING SCHEMA
────────────────────────────────────────────────────────
`sqlmesh plan` builds reports__dev.rep_target_attainment, rep_weekly_meeting
and rep_top_products. Nothing consumed them: marts_validation checked only
marts/bi/meta, and _valid_schemas below actively REJECTED "reports", so the
models were built on every run, validated by nothing, and impossible to
publish even deliberately.

That is the worst of the three states. An unbuilt model is obvious; a built
and published one is used; a built-but-unreachable one costs plan time every
run and silently accrues drift nobody sees.

Note that this changes what a default publish WRITES: three more files appear
under data/exports/parquet/<env>/. They cannot collide with existing names --
marts is fact_*/dim_*, bi is v_*, reports is rep_* -- so Power BI's existing
file paths are untouched. Drop "reports" from publish_schemas below if you
want them validated but not exported.
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

from typing import List

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The logical schemas SQLMesh builds that a consumer can read. `meta` is
# deliberately absent: it is observability, validated by marts_validation but
# not exported -- a Power BI refresh has no use for the file registry.
#
# Keep this in step with marts_validation's tuple in
# orchestration/assets/transformation.py and with serving/publish.py's
# --schemas choices. Three places, one list; a fourth schema added to SQLMesh
# and forgotten here is invisible rather than broken.
PUBLISHABLE_SCHEMAS = ("marts", "bi", "reports")


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
    #
    # Both are DuckDB-FILE-backend settings. With PG_CATALOG_HOST set, the
    # catalog is PostgreSQL and ducklake_catalog_path names a file no process
    # opens; shared/lake.py decides which backend is live, not this class.
    ducklake_catalog_path: str = "data/warehouse/catalog.ducklake"
    parquet_path: str = "data/warehouse/parquet"

    # ── Landing ────────────────────────────────────────────────────────────
    landing_skip_duplicate_files: bool = True
    """Skip a workbook whose sha256 is already ingested."""
    landing_evolve_schema: bool = True
    """Add columns a workbook gained; False raises instead."""

    # ── Publish (Power BI / Excel) ─────────────────────────────────────────
    enable_parquet_publish: bool = True
    """Publish to Parquet for Power BI."""
    enable_csv_publish: bool = False
    """Publish to CSV for Excel."""
    publish_schemas: List[str] = ["marts", "bi", "reports"]
    """Power BI's semantic model reads marts and does its own modelling in
    DAX; Excel and ad-hoc consumers read bi; reports are the pre-shaped
    report views (rep_*) that used to be built and then stranded."""
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
        bad = set(v) - set(PUBLISHABLE_SCHEMAS)
        if bad:
            raise ValueError(
                f"publish_schemas may only contain {sorted(PUBLISHABLE_SCHEMAS)}; "
                f"got {sorted(bad)}. If SQLMesh has gained a new serving schema, "
                f"add it to PUBLISHABLE_SCHEMAS in this module, to "
                f"marts_validation, and to publish.py's --schemas choices."
            )
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