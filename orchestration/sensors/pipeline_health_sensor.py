"""
orchestration/sensors/pipeline_health_sensor.py

Replaces sync_health_sensor.

WHAT THE OLD ONE WATCHED, AND WHY IT CANNOT
───────────────────────────────────────────
sync_health_sensor read `bi._sync_log` inside serving.db via
ServingLayerSync.get_sync_stats(): failed syncs, duration degradation against
a rolling average, and column-fingerprint drift per table.

serving.db, ServingLayerSync and _sync_log are all gone. Two of those three
questions moved rather than disappeared:

    failed syncs         -> meta.ingestion_batches.files_failed
    duration degradation -> meta.ingestion_batches.duration_seconds
    schema drift         -> meta.column_inventory

The third is the interesting one. Column fingerprints detected drift in the
SERVING copy -- downstream of everything, where a change had already
propagated. meta.column_inventory sees it in LANDING, at the boundary where a
supervisor's workbook actually changed, which is both earlier and more
actionable.

WHAT IT DOES NOT DO
───────────────────
It does not trigger a remediation job. The old sensor fired serving_only_job
on alert, which made sense when the fix was "re-run the sync". None of these
conditions is fixed by re-running: a failed workbook needs a human to look at
dead_letter, a stale source needs a phone call to a supervisor, and drift
needs someone to decide whether the new column is wanted. Re-running would
just produce the same alert on a schedule.

So it alerts and stops. If you later wire an on-alert action, make it a
notification, not a job.
"""
# NOTE: deliberately NO `from __future__ import annotations`.
#
# PEP 563 turns every annotation into a string, and Dagster resolves the
# `context` parameter by inspecting the actual class:
#
#   DagsterInvalidDefinitionError: Cannot annotate `context` parameter with
#   type AssetExecutionContext
#
# ...which reads as though the annotation is wrong when the annotation is the
# only correct one. Python 3.10+ handles `str | None` and `dict[str, X]`
# natively, so the import buys nothing here.

import json
import logging
from collections import deque
from typing import Any, Dict

from dagster import (
    DefaultSensorStatus,
    SensorEvaluationContext,
    SensorResult,
    sensor,
)

from orchestration.config import PipelineConfig
from orchestration.resources.duckdb_resource import DuckLakeResource
from orchestration.utils.constants import schema_for

logger = logging.getLogger(__name__)

ROLLING_WINDOW = 10
DURATION_DEGRADATION_FACTOR = 2.0
BUSINESS_LAG_WARN_DAYS = 7

_cfg = PipelineConfig()


def _default_cursor() -> str:
    return json.dumps({
        "durations": [],
        "last_batch_id": None,
        "column_fingerprint": {},
    })


def _column_fingerprints(conn, meta: str) -> Dict[str, str]:
    """
    One fingerprint per landing table: its ordered column names and types.

    Compared between runs, a change means a workbook gained, lost or retyped a
    column. Landing evolves permissively by design -- that is right for bronze
    and exactly why the change has to be surfaced rather than enforced.
    """
    rows = conn.execute(
        f'SELECT landing_table, column_name, data_type FROM "{meta}"."column_inventory" '
        f"ORDER BY landing_table, ordinal_position"
    ).fetchall()
    out: Dict[str, list] = {}
    for table, column, dtype in rows:
        out.setdefault(table, []).append(f"{column}:{dtype}")
    return {t: "|".join(cols) for t, cols in out.items()}


@sensor(
    name="pipeline_health_sensor",
    minimum_interval_seconds=900,
    default_status=DefaultSensorStatus.RUNNING,
    description=(
        "Alerts on failed files, stale sources, ingestion slowdown and "
        "landing schema drift. Reads meta.*; triggers nothing."
    ),
)
def pipeline_health_sensor(context: SensorEvaluationContext) -> SensorResult:
    cursor = json.loads(context.cursor or _default_cursor())
    env = _cfg.sqlmesh_env
    meta = schema_for("meta", env)

    try:
        conn = DuckLakeResource(read_only=True).get_connection()
    except Exception as exc:  # noqa: BLE001 -- lake locked or absent
        return SensorResult(
            skip_message=f"Lake unavailable ({exc}); will retry.",
            cursor=context.cursor or _default_cursor(),
        )

    try:
        batch = conn.execute(
            f'SELECT batch_id, started_at, duration_seconds, files_seen, '
            f'       files_ingested, files_failed, rows_landed '
            f'FROM "{meta}"."ingestion_batches" ORDER BY started_at DESC LIMIT 1'
        ).fetchone()
        if batch is None:
            return SensorResult(skip_message="No ingestion batches yet.",
                                cursor=context.cursor or _default_cursor())

        batch_id, started_at, duration, seen, ingested, failed, rows = batch

        stale = conn.execute(
            f'SELECT landing_table, business_lag_days FROM "{meta}"."freshness" '
            f"WHERE business_lag_days > ? ORDER BY business_lag_days DESC",
            [BUSINESS_LAG_WARN_DAYS],
        ).fetchall()

        fingerprints = _column_fingerprints(conn, meta)
    finally:
        conn.close()

    # Same batch as last evaluation: only re-report drift, which is computed
    # from schema rather than from the run.
    already_seen = cursor.get("last_batch_id") == str(batch_id)

    alerts: list[str] = []

    if not already_seen and failed:
        alerts.append(
            f"{failed} of {seen} file(s) failed in batch {batch_id}. "
            f"Check data/dead_letter/ and data/logs/failures_{batch_id}.txt."
        )

    if stale:
        alerts.append(
            "No transactions in the last "
            f"{BUSINESS_LAG_WARN_DAYS} days for: "
            + ", ".join(f"{t} ({int(d)}d)" for t, d in stale)
            + ". Expected over a holiday or a supervisor's leave."
        )

    # Duration trend. The rolling window excludes the current run so a single
    # slow batch is measured against normal, not against itself.
    durations = deque(cursor.get("durations", []), maxlen=ROLLING_WINDOW)
    if not already_seen and duration:
        prior = list(durations)
        durations.append(float(duration))
        if len(prior) >= 2:
            baseline = sum(prior) / len(prior)
            if duration > baseline * DURATION_DEGRADATION_FACTOR:
                alerts.append(
                    f"Ingestion took {duration:.0f}s against a {baseline:.0f}s "
                    f"average over the last {len(prior)} batches."
                )

    previous_fp = cursor.get("column_fingerprint", {})
    if previous_fp:
        for table, current in fingerprints.items():
            if table in previous_fp and previous_fp[table] != current:
                alerts.append(
                    f"Landing schema changed for {table}. A workbook gained, "
                    f"lost or retyped a column -- check meta.column_inventory."
                )
        for table in set(previous_fp) - set(fingerprints):
            alerts.append(f"Landing table {table} disappeared.")

    new_cursor = json.dumps({
        "durations": list(durations),
        "last_batch_id": str(batch_id),
        "column_fingerprint": fingerprints,
    })

    if alerts:
        for message in alerts:
            context.log.warning(message)
        return SensorResult(
            skip_message="; ".join(alerts)[:1000],
            cursor=new_cursor,
        )

    context.log.info(
        "Healthy: batch %s, %s file(s), %s row(s), %.0fs",
        batch_id, ingested, rows, duration or 0,
    )
    return SensorResult(skip_message="Pipeline health nominal", cursor=new_cursor)