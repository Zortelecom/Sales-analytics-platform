"""
orchestration/sensors/prod_promotion_sensor.py

Reports when a dev run is fresh enough to be promoted. Triggers nothing.

(2026-08) WHY THIS NO LONGER FIRES A JOB
────────────────────────────────────────
It used to watch serving_database and fire daily_pipeline_job. After the
rename to published_files that became a CYCLE, and a silent one:

    published_files materialises
        -> sensor fires daily_pipeline_job
        -> job is AssetSelection.all(), which CONTAINS published_files
        -> published_files materialises
        -> ...

run_key was f"prod_promotion_{asset_event.run_id}", unique per event, so
Dagster's run-key deduplication never collapsed them. With
enable_parquet_publish defaulting to True this starts on the first successful
pipeline run and does not stop on its own.

The cycle existed under the old architecture too, but serving_database was
produced by a job you ran deliberately, so it was rarely observed.

AND IT PROMOTED NOTHING ANYWAY
──────────────────────────────
The RunRequest carried tags={"environment": "prod"}. Nothing reads that tag.
SQLMeshResource.environment is bound to _cfg.sqlmesh_env when Definitions is
constructed, so the "prod" run was a second dev run wearing a label -- it
would have re-planned dev, re-audited dev and re-published dev, on a loop.

WHAT A REAL PROMOTION NEEDS, WHEN YOU WANT ONE
──────────────────────────────────────────────
Three things this file cannot supply on its own:

  1. A job whose selection EXCLUDES published_files, or the cycle returns.
  2. A way to run SQLMesh against prod. SQLMeshResource is configured at
     Definitions time, so either a second resource instance bound to a
     prod-targeted job, or a run-config field on the asset.
  3. A decision about what "prod" means here. Today prod and dev share one
     lake and differ only by SQLMesh schema suffix (marts vs marts__dev), so
     a promotion is `sqlmesh plan prod`, not a re-run of ingestion.

Until those exist, this alerts and stops -- the same shape as
pipeline_health_sensor, and for the same reason: a sensor that fires a job
which cannot fix the condition just reproduces the condition on a schedule.

default_status is STOPPED. Turn it on when you want the log line.
"""
from datetime import datetime, timedelta, timezone

from dagster import (
    AssetKey,
    DefaultSensorStatus,
    EventLogEntry,
    SensorEvaluationContext,
    SkipReason,
    asset_sensor,
)

LOOKBACK_PERIOD = timedelta(hours=24)
EXPECTED_ENVIRONMENT = "dev"


@asset_sensor(
    asset_key=AssetKey("published_files"),
    # NO job=. That is deliberate -- see the module docstring. Adding a job
    # here without also narrowing its asset selection reintroduces the cycle.
    name="prod_promotion_sensor",
    default_status=DefaultSensorStatus.STOPPED,
    description=(
        "Reports whether the latest published_files materialisation is recent "
        "enough and from the right environment to be promoted. Triggers "
        "nothing -- there is no prod-targeted job yet."
    ),
)
def prod_promotion_sensor(
    context: SensorEvaluationContext,
    asset_event: EventLogEntry,
) -> SkipReason:
    """Evaluate promotion eligibility and say so. No RunRequest is ever made."""
    materialization = asset_event.dagster_event.event_specific_data.materialization

    published_at = datetime.fromtimestamp(asset_event.timestamp, tz=timezone.utc)
    age = datetime.now(timezone.utc) - published_at

    if age > LOOKBACK_PERIOD:
        return SkipReason(
            f"Latest publish is {age.total_seconds() / 3600:.1f}h old "
            f"(limit {LOOKBACK_PERIOD.total_seconds() / 3600:.0f}h); not eligible."
        )

    env_value = materialization.metadata.get("environment")
    environment = env_value.value if env_value is not None else None

    if environment != EXPECTED_ENVIRONMENT:
        return SkipReason(
            f"Publish environment {environment!r} != {EXPECTED_ENVIRONMENT!r}; "
            f"not eligible."
        )

    context.log.info(
        "Dev publish from run %s is %.1fh old and eligible for promotion. "
        "No prod job is wired; promote by hand with `sqlmesh plan prod`.",
        asset_event.run_id, age.total_seconds() / 3600,
    )
    return SkipReason(
        f"Eligible: dev publish from run {asset_event.run_id}. "
        f"Promotion is manual (`sqlmesh plan prod`)."
    )