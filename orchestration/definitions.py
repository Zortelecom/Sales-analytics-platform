"""
orchestration/definitions.py  —  Dagster entry point

Changes vs. previous version
──────────────────────────────
* DuckDBResource now uses get_serving_db_path() so its database_path
  matches the env-aware filename introduced by the new serving layer
  (serving_dev.db for dev, serving.db for prod).
* All env-var reads centralised via PipelineConfig from orchestration/config.
* A concise start-up log lists the active feature flags so operators
  can verify their environment at a glance.
"""

import logging

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
from orchestration.sensors.prod_promotion_sensor import prod_promotion_sensor
from orchestration.sensors.sync_health_sensor import sync_health_sensor
from orchestration.resources import DuckDBResource, DuckLakeResource, SQLMeshResource
from orchestration.assets.data_quality import data_quality_full_report
from orchestration.assets.file_discovery import dead_letter_queue_check
from orchestration.utils.constants import get_serving_db_path
from orchestration.config import PipelineConfig

logger = logging.getLogger(__name__)

# ── Centralised configuration ──────────────────────────────────────────────
_cfg = PipelineConfig()

logger.info("Dagster definitions loaded — active flags: %s", _cfg.model_dump())


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
        data_quality_full_report,
        dead_letter_queue_check,
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
        sync_health_sensor,
        prod_promotion_sensor,
    ],
    resources={
        # ── DuckDB ──────────────────────────────────────────────────────────
        "duckdb": DuckDBResource(
            database_path=_cfg.duckdb_path or str(get_serving_db_path(_cfg.sqlmesh_env))
        ),

        # ── DuckLake ────────────────────────────────────────────────────────
        "ducklake": DuckLakeResource(),

        # ── SQLMesh ─────────────────────────────────────────────────────────
        "sqlmesh": SQLMeshResource(
            project_path="sqlmesh",
            environment=_cfg.sqlmesh_env,
            start_date="2025-01-01",
        ),
    },
)
