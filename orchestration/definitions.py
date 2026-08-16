"""
orchestration/definitions.py  —  Dagster entry point

(2026-08) Rewritten for the lake-native architecture.

ASSET GRAPH
───────────
    current_batch_id
    discovered_files → files_to_process → preprocessed_files
        ├─ sales_extract ─────┐
        ├─ targets_extract ───┤
        ├─ references_extract ┤→ landing_load → sqlmesh_models
        └─ kp_sd_extract ─────┘                      ↓
                                              marts_validation
                                                     ↓
                                              published_files → pipeline_complete

Four extract assets run in parallel; ONE load asset writes. That split is not
cosmetic: the DuckLake catalog is a DuckDB file and takes a single writer,
while Dagster materialises assets concurrently by default. Four writing assets
would contend for the catalog lock and fail intermittently. When the catalog
moves to PostgreSQL this can collapse back into four writers.

WHAT WAS REMOVED, AND WHY
─────────────────────────
    sales_seed / targets_seed / kp_sd_seed / references_seeds / seeds_metadata
        Replaced by *_extract + landing_load. There are no CSV seeds.
    serving_database
        Replaced by published_files. There is no serving.db to sync -- the
        interactive consumers attach the lake.
    DuckDBResource
        It opened serving.db.
    sync_health_sensor
        It read bi._sync_log inside serving.db. Replaced by
        pipeline_health_sensor over meta.*.
"""

import logging

from dagster import Definitions

from orchestration.assets import (
    current_batch_id,
    discovered_files,
    files_to_process,
    kp_sd_extract,
    landing_load,
    marts_validation,
    pipeline_complete,
    preprocessed_files,
    published_files,
    references_extract,
    sales_extract,
    sqlmesh_models,
    targets_extract,
)
from orchestration.assets.data_quality import (
    data_quality_checks,
    data_quality_report,
)
from orchestration.assets.file_discovery import dead_letter_queue_check
from orchestration.config import PipelineConfig
from orchestration.jobs.daily_pipeline import (
    daily_pipeline_job,
    ingestion_only_job,
    serving_only_job,
    transformation_only_job,
)
from orchestration.resources import DuckLakeResource, SQLMeshResource
from orchestration.schedules.daily_schedule import daily_6am_schedule, midday_schedule
from orchestration.sensors.file_sensor import new_file_sensor
from orchestration.sensors.pipeline_health_sensor import pipeline_health_sensor
from orchestration.sensors.prod_promotion_sensor import prod_promotion_sensor

logger = logging.getLogger(__name__)

_cfg = PipelineConfig()
logger.info("Dagster definitions loaded — config: %s", _cfg.model_dump())


assets = [
    # Batch tracking
    current_batch_id,

    # Discovery & preprocessing
    discovered_files,
    files_to_process,
    preprocessed_files,

    # Extraction (parallel) → landing (single writer)
    sales_extract,
    targets_extract,
    references_extract,
    kp_sd_extract,
    landing_load,

    # Transformation
    sqlmesh_models,
    marts_validation,

    # Data quality: an ASSET, because it is the only post-ingestion writer of
    # the lake and must not run alongside anything else that opens it.
    data_quality_report,

    # Publish for Power BI / Excel
    published_files,
    pipeline_complete,
]


defs = Definitions(
    assets=assets,
    asset_checks=[
        # Read-only, so it can run in parallel with anything. The WRITING
        # half is the data_quality_report asset above.
        data_quality_checks,
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
        pipeline_health_sensor,
        prod_promotion_sensor,
    ],
    resources={
        # Read-only by default: validation and quality assets have no business
        # writing, and a write attach would lock the catalog against SQLMesh.
        "ducklake": DuckLakeResource(read_only=True),
        "sqlmesh": SQLMeshResource(
            project_path="sqlmesh",
            environment=_cfg.sqlmesh_env,
            start_date=_cfg.sqlmesh_start_date,
        ),
    },
)