"""Sensor gate for promoting prod pipeline only after a recent successful dev materialization."""
from datetime import datetime, timedelta, timezone
from typing import Any

from dagster import (
    AssetKey,
    DefaultSensorStatus,
    RunRequest,
    SensorEvaluationContext,
    SkipReason,
    sensor,
)
from orchestration.jobs.daily_pipeline import daily_pipeline_job

LOOKBACK_PERIOD = timedelta(hours=24)
ASSET_KEY = AssetKey("serving_database")
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


@sensor(
    job=daily_pipeline_job,
    name="prod_promotion_sensor",
    default_status=DefaultSensorStatus.RUNNING,
    description=(
        "Only trigger the prod daily pipeline when a recent successful dev "
        "serving_database materialization exists."
    ),
)
def prod_promotion_sensor(context: SensorEvaluationContext):
    """Gate production pipeline execution on a dev serving_database materialization."""
    context.log.info("Evaluating prod promotion gate for serving_database asset")

    materialization = _get_latest_materialization(context.instance, ASSET_KEY)
    if materialization is None:
        context.log.info("Could not resolve latest serving_database materialization")
        return SkipReason("No serving_database materialization found or API unavailable.")

    timestamp_value = getattr(materialization, "timestamp", None) or getattr(materialization, "event_time", None)
    timestamp = _normalize_timestamp(timestamp_value)
    if timestamp is None:
        context.log.info("Unable to parse timestamp from latest serving_database materialization")
        return SkipReason("Invalid serving_database materialization timestamp.")

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    if now - timestamp > LOOKBACK_PERIOD:
        context.log.info(
            "Latest serving_database materialization is too old (%s); need one within last 24 hours.",
            timestamp.isoformat(),
        )
        return SkipReason("No recent dev serving_database materialization within the last 24 hours.")

    metadata_entries = getattr(materialization, "metadata_entries", None) or getattr(materialization, "metadata", None) or []
    environment = _extract_metadata_value(metadata_entries, "environment")
    if environment != EXPECTED_ENVIRONMENT:
        context.log.info(
            "Latest serving_database materialization environment %r does not match expected %r.",
            environment,
            EXPECTED_ENVIRONMENT,
        )
        return SkipReason("Latest serving_database materialization is not from dev.")

    run_id = getattr(materialization, "run_id", None)
    run_key = f"prod_promotion_{run_id or timestamp.isoformat()}"

    context.log.info(
        "Prod promotion gate passed; yielding RunRequest for daily pipeline (dev materialization at %s).",
        timestamp.isoformat(),
    )
    return RunRequest(
        run_key=run_key,
        tags={"trigger": "sensor", "gate": "prod_promotion", "environment": "prod"},
    )
