"""
orchestration/definitions.py  —  Dagster entry point

Changes vs. previous version
──────────────────────────────
* DuckDBResource now uses get_serving_db_path() so its database_path
  matches the env-aware filename introduced by the new serving layer
  (serving_dev.db for dev, serving.db for prod).
* ENABLE_CSV_EXPORT, ENABLE_PARQUET_EXPORT, ENABLE_QUACK, QUACK_TOKEN,
  EXPORT_BACKGROUND, and EXPORT_TIMEOUT_SECONDS are read from env-vars
  at start-up; they are consumed by the serving_database asset, not by
  Dagster resources (no new resource needed — the asset owns that config).
* A concise start-up log lists the active feature flags so operators
  can verify their environment at a glance.
"""

import logging
import os

from dagster import Definitions

from orchestration.assets import (
    current_batch_id,
    discovered_files,
    files_to_process,
    preprocessed_files,
    sales_seed,
    targets_seed,
    references_seeds,
    seeds_metadata,
    sqlmesh_models,
    marts_validation,
    serving_database,
    pipeline_complete,
)
from orchestration.jobs.daily_pipeline import (
    daily_pipeline_job,
    ingestion_only_job,
    transformation_only_job,
    serving_only_job,
)
from orchestration.schedules.daily_schedule import daily_6am_schedule, midday_schedule
from orchestration.sensors.file_sensor import new_file_sensor
from orchestration.resources import DuckDBResource, DuckLakeResource, SQLMeshResource
from orchestration.assets.data_quality import data_quality_full_report
from orchestration.utils.constants import get_serving_db_path, SQLMESH_ENV

logger = logging.getLogger(__name__)


# ── Runtime environment ────────────────────────────────────────────────────
_env = os.getenv("SQLMESH_ENV", SQLMESH_ENV)

# ── Feature flags (consumed by serving_database asset, logged here) ────────
_flags = {
    "SQLMESH_ENV":            _env,
    "ENABLE_CSV_EXPORT":      os.getenv("ENABLE_CSV_EXPORT",      "false"),
    "ENABLE_PARQUET_EXPORT":  os.getenv("ENABLE_PARQUET_EXPORT",  "false"),
    "ENABLE_QUACK":           os.getenv("ENABLE_QUACK",           "false"),
    "EXPORT_BACKGROUND":      os.getenv("EXPORT_BACKGROUND",      "false"),
    "EXPORT_TIMEOUT_SECONDS": os.getenv("EXPORT_TIMEOUT_SECONDS", "300"),
}
logger.info("Dagster definitions loaded — active flags: %s", _flags)


# ── All assets in dependency order ────────────────────────────────────────
assets = [
    # Batch tracking
    current_batch_id,

    # File discovery & preprocessing
    discovered_files,
    files_to_process,
    preprocessed_files,

    # Ingestion (seed creation)
    sales_seed,
    targets_seed,
    references_seeds,
    seeds_metadata,

    # Transformation (SQLMesh)
    sqlmesh_models,
    marts_validation,

    # Serving (BI database + optional exports)
    serving_database,
    pipeline_complete,
]


defs = Definitions(
    assets=assets,
    asset_checks=[
        # Runs after fact_sales is built.
        # blocking=False → pipeline never halts on failure.
        # Dagster UI shows a red badge on fact_sales with a per-audit
        # breakdown and the path to the full JSON quality report.
        data_quality_full_report,
    ],
    jobs=[
        daily_pipeline_job,
        ingestion_only_job,
        transformation_only_job,
        serving_only_job,
    ],
    schedules=[
        daily_6am_schedule,
        midday_schedule,
    ],
    sensors=[
        new_file_sensor,
    ],
    resources={
        # ── DuckDB ──────────────────────────────────────────────────────────
        # Points to the env-aware serving DB so Dagster's built-in DuckDB
        # sensor / IO manager and the reporting layer always agree on the
        # file path.  Override with DUCKDB_PATH for non-standard setups.
        "duckdb": DuckDBResource(
            database_path=os.getenv(
                "DUCKDB_PATH",
                str(get_serving_db_path(_env)),
            )
        ),

        # ── DuckLake ────────────────────────────────────────────────────────
        "ducklake": DuckLakeResource(),

        # ── SQLMesh ─────────────────────────────────────────────────────────
        "sqlmesh": SQLMeshResource(
            project_path="sqlmesh",
            environment=_env,
            start_date="2025-01-01",
        ),
    },
)
