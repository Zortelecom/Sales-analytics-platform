"""
orchestration/assets/serving.py

(2026-08) Rewritten. The previous version wired ServingLayerSync, QuackConfig
and an async ExportEventBus -- all deleted with the serving redesign.

WHAT THE SERVING ASSET DOES NOW
───────────────────────────────
Nothing, for most consumers. Streamlit, Superset and Metabase attach the lake
and read bi.* directly, so there is no copy to make and no sync to run: the
moment `sqlmesh plan` finishes, they are current.

What remains is publishing files for the two consumers that cannot attach:
Power BI reads Parquet (marts -- its semantic model is a star schema built in
DAX, not the bi views) and Excel reads CSV.

That is why this is `published_files` rather than `serving_database`: the
asset no longer produces a database.

⚠ prod_promotion_sensor watches AssetKey("serving_database") and reads its
`environment` metadata. Renaming the asset breaks that sensor -- it is updated
in the same change. If you keep an old sensor pointing at the old key it will
simply never fire, silently.
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

import time
from datetime import datetime

from dagster import AssetExecutionContext, AssetIn, Failure, MetadataValue, asset

from orchestration.config import PipelineConfig
from serving.publish import PublishConfig, publish

_cfg = PipelineConfig()


@asset(
    group_name="serving",
    description=(
        "Publishes marts and bi to Parquet (Power BI) and CSV (Excel). "
        "Interactive tools attach the lake directly and need nothing here."
    ),
    compute_kind="duckdb",
    # Depends on data_quality_report, not just marts_validation. That asset is
    # the only post-ingestion WRITER of the lake, and a DuckDB file catalog
    # admits many readers or one writer -- publishing while it wrote produced
    #   IO Error: Failed to attach DuckLake MetaData ...
    #   File is already open in python.exe (PID ...)
    # An explicit dependency is how you serialise in Dagster; there is nothing
    # in the data flow that needs it, only the lock.
    ins={"marts_validation": AssetIn(), "data_quality_report": AssetIn()},
)
def published_files(
    context: AssetExecutionContext,
    marts_validation: dict,
    data_quality_report: dict,
) -> dict:
    """Publish the file-based consumers' copies, or skip if both are disabled."""
    formats = [
        f for f, on in (
            ("parquet", _cfg.enable_parquet_publish),
            ("csv", _cfg.enable_csv_publish),
        ) if on
    ]

    if not formats:
        context.log.info(
            "Both publishes disabled. Interactive consumers read the lake "
            "directly and are already current."
        )
        context.add_output_metadata({"published": "disabled"})
        return {"status": "skipped", "formats": []}

    config = PublishConfig(
        environment=_cfg.sqlmesh_env,
        formats=formats,
        schemas=list(_cfg.publish_schemas),
        compression=_cfg.parquet_compression,
        csv_delimiter=_cfg.csv_delimiter,
    )

    context.log.info(
        "Publishing %s from %s (env=%s)",
        ", ".join(formats), ", ".join(config.schemas), config.environment,
    )

    t0 = time.perf_counter()
    try:
        results = publish(config)
    except Exception as exc:
        raise Failure(description=f"Publish failed: {exc}") from exc
    duration = time.perf_counter() - t0

    rows = {name: max(per_format.values()) for name, per_format in results.items()}
    empty = sorted(n for n, r in rows.items() if r == 0)

    context.add_output_metadata({
        "environment": config.environment,
        "formats": MetadataValue.json(formats),
        "schemas": MetadataValue.json(config.schemas),
        "objects_published": len(results),
        "total_rows": int(sum(rows.values())),
        "empty_objects": MetadataValue.json(empty),
        "export_root": str(config.export_root),
        "duration_seconds": round(duration, 2),
        "detail": MetadataValue.md(
            "\n".join(f"- `{n}`: {r:,} rows" for n, r in sorted(rows.items()))
        ),
    })

    if empty:
        context.log.warning("Published but empty: %s", empty)

    context.log.info("Published %d object(s) in %.1fs", len(results), duration)
    return {
        "status": "success",
        "environment": config.environment,
        "formats": formats,
        "objects": len(results),
        "export_root": str(config.export_root),
    }


@asset(
    group_name="serving",
    description="Final pipeline completion marker.",
    ins={"published_files": AssetIn(), "marts_validation": AssetIn()},
)
def pipeline_complete(
    context: AssetExecutionContext,
    published_files: dict,
    marts_validation: dict,
) -> dict:
    """
    Completion summary.

    `available_for_bi` now means the MARTS are built, not that a serving file
    validated -- interactive consumers read the lake, so their readiness is
    marts_validation's business. The publish only gates Power BI and Excel.
    """
    summary = {
        "timestamp": datetime.now().isoformat(),
        "run_id": context.run.run_id,
        "environment": published_files.get("environment", _cfg.sqlmesh_env),
        "objects_validated": len(marts_validation),
        "rows_validated": int(sum(v for v in marts_validation.values() if v > 0)),
        "publish_status": published_files.get("status"),
        "published_formats": published_files.get("formats", []),
        "available_for_bi": bool(marts_validation),
    }

    context.add_output_metadata({
        "pipeline_status": "completed",
        "environment": summary["environment"],
        "available_for_bi": summary["available_for_bi"],
        "publish_status": summary["publish_status"],
    })

    context.log.info("Pipeline completed: %s", summary)
    return summary