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
                                          data_quality_report
                                                     ↓
                                              published_files → pipeline_complete

data_quality_report sits BETWEEN validation and publish, and that ordering is
a lock, not a data dependency: it is the only post-ingestion writer of the
lake, and a DuckDB file catalog admits many readers or one writer. Publishing
while it wrote produced

    IO Error: Failed to attach DuckLake MetaData ...
    File is already open in python.exe (PID ...)

An explicit dependency is how you serialise in Dagster.

Four extract assets run in parallel; ONE load asset writes. That split is not
cosmetic: the DuckLake catalog is a DuckDB file and takes a single writer,
while Dagster materialises assets concurrently by default. Four writing assets
would contend for the catalog lock and fail intermittently. When the catalog
moves to PostgreSQL this can collapse back into four writers.



LAUNCH
──────
Prefer module loading over `-f`:

    [tool.dagster]
    module_name = "orchestration.definitions"
    code_location_name = "sales_analytics_platform"

...in pyproject.toml, then plain `dagster dev` from the project root. Loading
by path imports this file as an ad-hoc module, which can produce two
PipelineConfig instances from one process.

SET DAGSTER_HOME. Without it `dagster dev` uses a temporary directory and
discards run history AND SENSOR CURSORS on every restart. pipeline_health_
sensor keeps its rolling duration window and its per-table column
fingerprints in its cursor, so schema-drift detection -- the thing that sensor
exists for -- cannot fire across a restart until DAGSTER_HOME is persistent.
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
    quality_only_job,
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
        # quality_only_job selects AssetSelection.groups("quality"). VERIFY
        # that data_quality_report actually declares group_name="quality" --
        # a group-scoped job whose group matches no asset is not an error in
        # Dagster, it is an empty job that succeeds instantly.
        quality_only_job,
        serving_only_job,
    ],
    schedules=[
        daily_6am_schedule,
        midday_schedule,
    ],
    sensors=[
        new_file_sensor,
        pipeline_health_sensor,
        # Reports promotion eligibility; targets no job. It used to fire
        # daily_pipeline_job, which materialises the very asset it watches --
        # an unbounded loop that run_key deduplication could not stop because
        # the key included the run id. STOPPED by default.
        prod_promotion_sensor,
    ],
    resources={
        # Read-only by default: validation and quality assets have no business
        # writing, and a write attach would lock the catalog against SQLMesh.
        # DuckLakeResource now derives its CREDENTIAL role from this flag too
        # (reader vs writer), so read paths stop attaching PostgreSQL as the
        # writing user.
        #
        # NOTE: data_quality_report and data_quality_checks construct their own
        # DuckLakeResource inline rather than taking this one, so config set
        # here does not reach them. Worth consolidating when data_quality.py is
        # next touched.
        "ducklake": DuckLakeResource(read_only=True),
        "sqlmesh": SQLMeshResource(
            project_path="sqlmesh",
            environment=_cfg.sqlmesh_env,
            start_date=_cfg.sqlmesh_start_date,
            # `sqlmesh audit` has no --environment option and targets the
            # DEFAULT target environment. Set False if the
            # SQLMESH__DEFAULT_TARGET_ENVIRONMENT override proves unreliable --
            # `sqlmesh plan` already enforces blocking audits on the models it
            # builds, so this only costs the audit_status metadata.
            run_audits=True,
        ),
    },
)