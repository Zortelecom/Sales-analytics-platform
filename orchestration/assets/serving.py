"""
orchestration/assets/serving.py

Dagster assets for the serving layer.

Changes vs. the previous version
──────────────────────────────────
* Uses the new ServingConfig / QuackConfig API from serving.config.
* Wires CsvExporter and ParquetExporter via ExportEventBus so exports run
  concurrently *after* the sync without blocking the sync itself.
* Supports two dispatch modes driven by the EXPORT_BACKGROUND env-var:
    - False (default) → publish_and_wait  — Dagster waits; export results
                        land in asset metadata on the same run.
    - True            → publish_background — fire-and-forget daemon thread;
                        useful when exports are slow and BI freshness is
                        the priority.
* Supports Quack mode when ENABLE_QUACK=true and QUACK_TOKEN are set.
* Reports per-exporter results and the serving-DB path in asset metadata.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from dagster import AssetExecutionContext, AssetIn, Failure, asset

# ── project root on sys.path ────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from serving.config import QuackConfig, ServingConfig
from serving.export import CsvExporter, ExportEventBus, ParquetExporter
from serving.sync import ServingLayerSync

from orchestration.utils.constants import (
    CSV_EXPORTS_DIR,
    PARQUET_EXPORTS_DIR,
    get_serving_db_path,
    SQLMESH_ENV,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _build_config(env: str, context: AssetExecutionContext) -> ServingConfig:
    """
    Build ServingConfig from environment variables so callers never have to
    touch Python code to change runtime behaviour.

    Env-vars read:
        ENABLE_QUACK        "true" / "false"   (default false)
        QUACK_HOST          hostname            (default localhost)
        QUACK_PORT          integer             (default 9494)
        QUACK_TOKEN         secret string       (required when ENABLE_QUACK=true)
    """
    enable_quack = os.getenv("ENABLE_QUACK", "false").lower() == "true"

    quack_cfg: QuackConfig | None = None
    if enable_quack:
        token = os.getenv("QUACK_TOKEN", "")
        if not token:
            context.log.warning(
                "ENABLE_QUACK=true but QUACK_TOKEN is not set — "
                "falling back to file-swap mode."
            )
            enable_quack = False
        else:
            quack_cfg = QuackConfig(
                host=os.getenv("QUACK_HOST", "localhost"),
                port=int(os.getenv("QUACK_PORT", "9494")),
                token=token,
            )
            context.log.info(
                "Quack mode enabled → %s:%s",
                quack_cfg.host,
                quack_cfg.port,
            )

    config = ServingConfig(environment=env, quack=quack_cfg)
    config.normalize(project_root=_PROJECT_ROOT)
    return config


def _build_export_bus(env: str, context: AssetExecutionContext) -> ExportEventBus | None:
    """
    Build an ExportEventBus with whichever exporters are enabled via env-vars.

    Env-vars read:
        ENABLE_CSV_EXPORT           "true" / "false"   (default false)
        CSV_DELIMITER               single char         (default ,)
        ENABLE_PARQUET_EXPORT       "true" / "false"   (default false)
        PARQUET_COMPRESSION         codec name          (default snappy)

    Returns None when no exporters are enabled (skips bus entirely).
    """
    exporters = []

    if os.getenv("ENABLE_CSV_EXPORT", "false").lower() == "true":
        csv_dir = CSV_EXPORTS_DIR / env
        csv_dir.mkdir(parents=True, exist_ok=True)
        delimiter = os.getenv("CSV_DELIMITER", ",")
        exporters.append(CsvExporter(str(csv_dir), delimiter=delimiter))
        context.log.info("CSV export enabled → %s (delimiter=%r)", csv_dir, delimiter)

    if os.getenv("ENABLE_PARQUET_EXPORT", "false").lower() == "true":
        pq_dir = PARQUET_EXPORTS_DIR / env
        pq_dir.mkdir(parents=True, exist_ok=True)
        compression = os.getenv("PARQUET_COMPRESSION", "snappy")
        exporters.append(ParquetExporter(str(pq_dir), compression=compression))
        context.log.info(
            "Parquet export enabled → %s (compression=%s)", pq_dir, compression
        )

    if not exporters:
        return None

    bus = ExportEventBus()
    for exp in exporters:
        bus.subscribe(exp)
    return bus


def _export_metadata(
    bus: ExportEventBus | None,
    background: bool,
) -> dict[str, Any]:
    """
    Return a metadata dict describing the export configuration so it appears
    in the Dagster asset materialisation panel.
    """
    if bus is None:
        return {"exports": "disabled"}
    mode = "background" if background else "blocking"
    return {
        "export_mode": mode,
        "exporters": [type(e).__name__ for e in bus.subscribers],
    }


def _fire_background_exports(
    bus: ExportEventBus,
    sync: ServingLayerSync,
    summary: dict,
    context: AssetExecutionContext,
) -> None:
    """
    Build a SyncCompletedEvent and publish it in a background thread.

    Mirrors the cli.py cmd_sync background path so both entry points
    behave identically.  The daemon thread is fire-and-forget: the
    Dagster asset completes without waiting for export I/O to finish.
    """
    from serving.export.events import SyncCompletedEvent

    succeeded = [
        m["table"] for m in sync.sync_metadata if m.get("status") == "success"
    ]
    event = SyncCompletedEvent(
        environment=sync.config.environment,
        serving_path=sync.config.serving_path,
        bi_schema=sync.config.bi_schema,
        tables=succeeded,
        mode=summary.get("mode", "unknown"),
    )
    thread = bus.publish_background(event)
    context.log.info(
        "Background exports dispatched (thread: %s). Asset returning immediately.",
        thread.name,
    )


# ── assets ───────────────────────────────────────────────────────────────────

@asset(
    group_name="serving",
    description=(
        "Syncs Gold mart tables from DuckLake into serving.db for BI. "
        "Supports file-swap (default) and Quack server mode. "
        "Optionally triggers async CSV / Parquet exports via ExportEventBus."
    ),
    compute_kind="python",
    ins={"marts_validation": AssetIn()},
)
def serving_database(
    context: AssetExecutionContext,
    marts_validation: pd.DataFrame,
) -> dict:
    """
    1. Build ServingConfig (Quack or file-swap) from env-vars.
    2. Optionally wire an ExportEventBus with CSV / Parquet exporters.
    3. Run the sync.
    4. Validate the resulting serving DB.
    5. Emit rich metadata for the Dagster UI.
    """
    env = os.getenv("SQLMESH_ENV", SQLMESH_ENV)
    background_exports = os.getenv("EXPORT_BACKGROUND", "false").lower() == "true"

    # ── config ────────────────────────────────────────────────────────────────
    config = _build_config(env, context)
    export_bus = _build_export_bus(env, context)

    # ── sync ──────────────────────────────────────────────────────────────────
    # sync() only accepts dry_run; dispatch mode is controlled by what we pass
    # to the constructor:
    #   blocking  → pass export_bus to ServingLayerSync; _trigger_exports()
    #               calls publish_and_wait internally before sync() returns.
    #   background → withhold the bus from ServingLayerSync so sync() returns
    #               as soon as tables are written, then we fire the bus here
    #               via publish_background (same pattern as cli.py cmd_sync).
    if background_exports and export_bus is not None:
        sync = ServingLayerSync(config, export_bus=None)
    else:
        sync = ServingLayerSync(config, export_bus=export_bus)

    try:
        context.log.info(
            "Starting serving sync (env=%s, strategy=%s)",
            env,
            "quack" if config.quack else "file-swap",
        )

        summary = sync.sync(dry_run=False)

        # Background export: fire after sync returns, don't wait
        if background_exports and export_bus is not None:
            if summary.get("status") in ("success", "partial_success"):
                _fire_background_exports(export_bus, sync, summary, context)
            else:
                context.log.warning(
                    "Sync status is %r — skipping background exports.",
                    summary.get("status"),
                )

        # ── validation ────────────────────────────────────────────────────────
        is_valid = sync.validate_serving_db()

        serving_path = get_serving_db_path(env)
        size_mb = (
            serving_path.stat().st_size / (1024 * 1024)
            if serving_path.exists()
            else 0.0
        )

        export_meta = _export_metadata(export_bus, background_exports)

        context.add_output_metadata(
            {
                "environment": env,
                "sync_strategy": "quack" if config.quack else "file-swap",
                "serving_path": str(serving_path),
                "serving_exists": serving_path.exists(),
                "validation_passed": is_valid,
                "size_mb": round(size_mb, 2),
                "marts_schema": config.marts_schema,
                **export_meta,
            }
        )

        context.log.info(
            "Serving sync complete (valid=%s, size=%.1f MB).", is_valid, size_mb
        )

        return {
            "status": "success",
            "serving_path": str(serving_path),
            "validated": is_valid,
            "environment": env,
            "exports_enabled": export_bus is not None,
        }

    except Exception as exc:
        raise Failure(description=f"Serving layer sync failed: {exc}") from exc


@asset(
    group_name="serving",
    description="Final pipeline completion marker.",
    ins={"serving_database": AssetIn()},
)
def pipeline_complete(
    context: AssetExecutionContext,
    serving_database: dict,
) -> dict:
    """Emit a pipeline-complete summary with BI readiness status."""

    summary = {
        "timestamp": datetime.now().isoformat(),
        "run_id": context.run.run_id,
        "data_freshness": "current",
        "serving_db": serving_database["serving_path"],
        "available_for_bi": serving_database["validated"],
        "environment": serving_database.get("environment", "dev"),
        "exports_enabled": serving_database.get("exports_enabled", False),
    }

    context.add_output_metadata(
        {
            "pipeline_status": "completed",
            "available_for_bi": serving_database["validated"],
            "environment": summary["environment"],
        }
    )

    context.log.info("🎉 Pipeline completed successfully! %s", summary)
    return summary