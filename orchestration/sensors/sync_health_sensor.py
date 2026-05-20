"""Sensor to monitor sync health and alert on failures or performance degradation"""
import logging
import json
from collections import deque
from datetime import datetime
from typing import Optional
from typing import Union

from dagster import (
    sensor,
    RunRequest,
    SkipReason,
    SensorExecutionContext,
    DefaultSensorStatus,
)
from orchestration.jobs.daily_pipeline import serving_only_job
from serving.sync import ServingLayerSync
from serving.config import ServingConfig

logger = logging.getLogger(__name__)

# Configuration
ROLLING_WINDOW_SIZE = 10  # Track last N syncs for rolling average
DURATION_DEGRADATION_THRESHOLD = 2.0  # Alert if avg_duration_ms > rolling_avg * 2


def get_default_cursor_state() -> str:
    """Initialize cursor state with empty rolling window"""
    return json.dumps({
        "durations_ms": [],  # Rolling window of duration_ms values
        "last_sync_timestamp": None,
    })


@sensor(
    job=serving_only_job,
    name="sync_health_sensor",
    default_status=DefaultSensorStatus.RUNNING,
    description="Monitor sync health: alert on failed_syncs > 0 or duration degradation",
)
def sync_health_sensor(context: SensorExecutionContext) -> Union[RunRequest, SkipReason, None]:
    """
    Monitor _sync_log after serving_only_job completes.
    
    Raises alerts (via context.log.warning) and yields RunRequest if:
    1. failed_syncs > 0 (sync failures detected)
    2. avg_duration_ms > rolling_avg * 2 (performance degradation)
    
    Otherwise, yields SkipReason to skip the next run request.
    """
    
    # Initialize cursor if needed
    if context.cursor is None:
        context.cursor = get_default_cursor_state()
    
    try:
        cursor_data = json.loads(context.cursor)
    except json.JSONDecodeError:
        logger.warning("Invalid cursor state, resetting")
        cursor_data = json.loads(get_default_cursor_state())
    
    # Get serving config and sync stats
    try:
        config = ServingConfig()
        config.normalize()
        sync = ServingLayerSync(config)
        stats = sync.get_sync_stats()
    except Exception as exc:
        logger.error("Failed to initialize sync stats: %s", exc)
        return SkipReason(
            f"Could not read sync stats: {exc}. Will retry on next sensor check."
        )
    
    # No stats available yet (first run)
    if not stats:
        logger.info("No sync stats available yet")
        return SkipReason("Serving database not initialized yet")
    
    last_sync = stats.get("last_sync")
    failed_syncs = stats.get("failed_syncs", 0)
    avg_duration_ms = stats.get("avg_duration_ms", 0)
    successful_syncs = stats.get("successful_syncs", 0)
    
    # Skip if this is the same sync we already processed
    if last_sync and cursor_data.get("last_sync_timestamp") == str(last_sync):
        logger.debug("Cursor already processed this sync, skipping")
        return SkipReason("Already processed this sync event")
    
    # ─────────────────────────────────────────────────────────────────────────
    # Check 1: Failed syncs
    # ─────────────────────────────────────────────────────────────────────────
    if failed_syncs > 0:
        message = (
            f"⚠️ SYNC ALERT: {failed_syncs} failed sync(s) detected. "
            f"Total syncs: {stats.get('total_syncs', 0)}, "
            f"Successful: {successful_syncs}"
        )
        context.log.warning(message)
        should_alert = True
    else:
        should_alert = False
    
    # ─────────────────────────────────────────────────────────────────────────
    # Check 2: Duration degradation (vs rolling average)
    # ─────────────────────────────────────────────────────────────────────────
    durations_ms = cursor_data.get("durations_ms", [])
    
    # Add current duration to rolling window
    if avg_duration_ms and avg_duration_ms > 0:
        deque_durations = deque(durations_ms, maxlen=ROLLING_WINDOW_SIZE)
        deque_durations.append(avg_duration_ms)
        durations_ms = list(deque_durations)
    
    # Calculate rolling average (need at least 2 data points)
    rolling_avg_ms = None
    if len(durations_ms) >= 2:
        rolling_avg_ms = sum(durations_ms[:-1]) / len(durations_ms[:-1])  # Exclude current
        
        if avg_duration_ms > rolling_avg_ms * DURATION_DEGRADATION_THRESHOLD:
            message = (
                f"⚠️ PERFORMANCE ALERT: Sync duration degradation detected. "
                f"Current avg: {avg_duration_ms:.0f}ms, "
                f"Rolling avg (last {len(durations_ms)-1} syncs): {rolling_avg_ms:.0f}ms, "
                f"Threshold: {rolling_avg_ms * DURATION_DEGRADATION_THRESHOLD:.0f}ms"
            )
            context.log.warning(message)
            should_alert = True
    
    # Update cursor with current state
    cursor_data["durations_ms"] = durations_ms
    cursor_data["last_sync_timestamp"] = str(last_sync) if last_sync else None
    context.cursor = json.dumps(cursor_data)
    
    # ─────────────────────────────────────────────────────────────────────────
    # Yield result
    # ─────────────────────────────────────────────────────────────────────────
    if should_alert:
        # Alert triggered: yield RunRequest to kick off a diagnostic/remediation job
        context.log.warning(
            "Sync health alert triggered. Would dispatch remediation job here."
        )
        return RunRequest(
            tags={
                "alert_type": "sync_health",
                "failed_syncs": str(failed_syncs),
                "avg_duration_ms": f"{avg_duration_ms:.0f}",
                "rolling_avg_ms": f"{rolling_avg_ms:.0f}" if rolling_avg_ms else "N/A",
            }
        )
    
    # No alert: skip this run
    logger.info(
        "Sync health OK: %d failed, avg duration %.0f ms",
        failed_syncs,
        avg_duration_ms or 0,
    )
    return SkipReason("Sync health nominal")
