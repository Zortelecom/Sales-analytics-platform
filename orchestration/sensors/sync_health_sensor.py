"""Sensor to monitor sync health and alert on failures or performance degradation"""
import logging
import json
from collections import deque
from typing import Dict
from typing import Union
from pathlib import Path

import duckdb
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
    """Initialize cursor state with empty rolling window and fingerprints"""
    return json.dumps({
        "durations_ms": [],  # Rolling window of duration_ms values
        "last_sync_timestamp": None,
        "previous_fingerprints": {},  # {table_name: column_fingerprint}
    })


def _get_latest_fingerprints(config: ServingConfig) -> Dict[str, str]:
    """
    Query the latest column fingerprints from _sync_log for each table.
    
    Returns dict: {table_name: column_fingerprint, ...}
    """
    serving_path = Path(config.serving_path)
    if not serving_path.exists():
        return {}
    
    try:
        conn = duckdb.connect(str(serving_path), read_only=True)
        # Get the latest fingerprint for each table (most recent sync_timestamp)
        result = conn.execute(f"""
            SELECT DISTINCT
                target_table,
                column_fingerprint
            FROM (
                SELECT
                    target_table,
                    column_fingerprint,
                    ROW_NUMBER() OVER (PARTITION BY target_table ORDER BY sync_timestamp DESC) as rn
                FROM {config.bi_schema}._sync_log
                WHERE status = 'success' AND column_fingerprint IS NOT NULL
            ) t
            WHERE rn = 1
            ORDER BY target_table
        """).fetchall()
        
        conn.close()
        
        # Build dict: extract table name from full path (schema.table -> table)
        fingerprints = {}
        for full_table, fp in result:
            # Extract just the table name (last component after '.')
            table_name = full_table.split(".")[-1] if "." in full_table else full_table
            fingerprints[table_name] = fp
        
        logger.debug("Latest fingerprints: %s", fingerprints)
        return fingerprints
    except duckdb.Error as exc:
        logger.error("Failed to get latest fingerprints: %s", exc)
        return {}


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
    # Check 2: Column fingerprint changes (schema drift detection)
    # ─────────────────────────────────────────────────────────────────────────
    current_fingerprints = _get_latest_fingerprints(config)
    previous_fingerprints = cursor_data.get("previous_fingerprints", {})
    
    fingerprint_changes = []
    
    if previous_fingerprints:  # Only check if we have a baseline
        for table, current_fp in current_fingerprints.items():
            previous_fp = previous_fingerprints.get(table)
            if previous_fp and current_fp != previous_fp:
                fingerprint_changes.append((table, previous_fp, current_fp))
        
        if fingerprint_changes:
            for table, old_fp, new_fp in fingerprint_changes:
                message = (
                    f"⚠️ SCHEMA ALERT: Column fingerprint changed for table '{table}'. "
                    f"Previous: {old_fp}, Current: {new_fp}. "
                    f"This indicates the table schema has changed."
                )
                context.log.warning(message)
            should_alert = True
    
    # ─────────────────────────────────────────────────────────────────────────
    # Check 3: Duration degradation (vs rolling average)
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
    cursor_data["previous_fingerprints"] = current_fingerprints  # Store for next comparison
    context.cursor = json.dumps(cursor_data)
    
    # ─────────────────────────────────────────────────────────────────────────
    # Yield result
    # ─────────────────────────────────────────────────────────────────────────
    if should_alert:
        # Alert triggered: yield RunRequest to kick off a diagnostic/remediation job
        context.log.warning(
            "Sync health alert triggered. Would dispatch remediation job here."
        )
        tags = {
            "alert_type": "sync_health",
            "failed_syncs": str(failed_syncs),
            "avg_duration_ms": f"{avg_duration_ms:.0f}",
            "rolling_avg_ms": f"{rolling_avg_ms:.0f}" if rolling_avg_ms else "N/A",
        }
        
        if fingerprint_changes:
            tags["schema_changes"] = ",".join(f"{t}({o}->{n})" for t, o, n in fingerprint_changes)
        
        return RunRequest(tags=tags)
    
    # No alert: skip this run
    logger.info(
        "Sync health OK: %d failed, avg duration %.0f ms",
        failed_syncs,
        avg_duration_ms or 0,
    )
    return SkipReason("Sync health nominal")
