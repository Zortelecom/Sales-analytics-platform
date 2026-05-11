"""Dagster definitions - main entry point"""
import os
from dagster import Definitions

from orchestration.assets import (
    current_batch_id,  # ✅ Added
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

# Import jobs
from orchestration.jobs.daily_pipeline import (
    daily_pipeline_job,
    ingestion_only_job,
    transformation_only_job,
    serving_only_job,
)

# Import schedules
from orchestration.schedules.daily_schedule import daily_6am_schedule, midday_schedule

# Import sensors
from orchestration.sensors.file_sensor import new_file_sensor

# Import resources
from orchestration.resources import DuckDBResource, DuckLakeResource, SQLMeshResource

# Import asset checks
from orchestration.assets.data_quality import data_quality_full_report

# All assets in dependency order
assets = [
    # Batch tracking
    current_batch_id,  #

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

    # Serving (BI database)
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
        "duckdb": DuckDBResource(
            database_path=os.getenv("DUCKDB_PATH", "data/warehouse/serving.db")
        ),
        "ducklake": DuckLakeResource(),
        "sqlmesh": SQLMeshResource(
            project_path="sqlmesh",
            environment=os.getenv("SQLMESH_ENV", "dev"),
            start_date="2025-01-01"
        ),
    },
)
