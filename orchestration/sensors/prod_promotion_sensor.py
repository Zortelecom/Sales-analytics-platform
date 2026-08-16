"""Sensor gate: promote to prod only after a recent successful dev run.

(2026-08) Watches published_files, formerly serving_database. The rename
matters more than it looks -- an asset_sensor pointed at a key that no asset
emits does not error, it simply never fires, and the prod pipeline silently
stops being promoted.

It still gates on the `environment` metadata, which published_files emits.
"""
from datetime import datetime, timedelta, timezone
from typing import Any
from dagster import (
    AssetKey,
    DefaultSensorStatus,
    RunRequest,
    SensorEvaluationContext,
    SkipReason,
    asset_sensor,
    EventLogEntry
)
from orchestration.jobs.daily_pipeline import daily_pipeline_job

LOOKBACK_PERIOD = timedelta(hours=24)
ASSET_KEY = AssetKey("published_files")
EXPECTED_ENVIRONMENT = "dev"



def _get_latest_materialization(instance: Any, asset_key: AssetKey) -> Any:
    """Return the latest materialization event for the given asset key."""
    if hasattr(instance, "get_latest_materialization"):
        try:
            return instance.get_latest_materialization(asset_key=asset_key)
        except Exception:
            pass

    event_storage = getattr(instance, "event_storage", None)
    if event_storage is not None and hasattr(event_storage, "get_latest_materialization"):
        try:
            return event_storage.get_latest_materialization(asset_key)
        except Exception:
            pass

    return None


def _normalize_timestamp(timestamp: Any) -> datetime | None:
    if timestamp is None:
        return None
    if isinstance(timestamp, datetime):
        return timestamp
    if isinstance(timestamp, str):
        try:
            return datetime.fromisoformat(timestamp)
        except ValueError:
            return None
    return None


def _extract_metadata_value(metadata_entries: Any, label: str) -> Any:
    if not metadata_entries:
        return None

    for entry in metadata_entries:
        entry_label = getattr(entry, "label", None) or getattr(entry, "name", None)
        if not entry_label or entry_label.lower() != label.lower():
            continue

        entry_data = getattr(entry, "entry_data", entry)
        value = getattr(entry_data, "text", None)
        if value is None:
            value = getattr(entry_data, "value", None)
        return value

    return None


@asset_sensor(
    asset_key=AssetKey("published_files"),
    job=daily_pipeline_job,
    name="prod_promotion_sensor",
    default_status=DefaultSensorStatus.RUNNING,
    description=(
        "Only trigger the prod daily pipeline after a recent dev serving_database materialization.."
    ),
)
def prod_promotion_sensor(context: SensorEvaluationContext, asset_event: EventLogEntry):
    """Gate production pipeline execution on a dev serving_database materialization."""
    context.log.info("Evaluating prod promotion gate for serving_database asset")

    materialization = asset_event.dagster_event.event_specific_data.materialization
    
    ts = datetime.fromtimestamp(asset_event.timestamp, tz=timezone.utc)
    if datetime.now(timezone.utc) - ts > LOOKBACK_PERIOD:
        context.log.info("Latest serving_database materialization is older than 24h.")
        return
    
    env_value = materialization.metadata.get("environment")
    environment = env_value.value if env_value is not None else None
    if environment != EXPECTED_ENVIRONMENT:
        context.log.info("Materialization environment %r != %r.", environment, EXPECTED_ENVIRONMENT)
        return

    yield RunRequest(
        run_key=f"prod_promotion_{asset_event.run_id}",
        tags={"trigger": "sensor", "gate": "prod_promotion", "environment": "prod"},
    )