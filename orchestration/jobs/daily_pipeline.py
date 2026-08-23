"""
orchestration/jobs/daily_pipeline.py

(2026-08) Aligned with the lake-native asset groups.

THE GROUPS, AND WHAT EACH JOB CAN ACTUALLY RUN ALONE
────────────────────────────────────────────────────
    ingestion       current_batch_id, discovered_files, files_to_process,
                    preprocessed_files, *_extract, landing_load
    transformation  sqlmesh_models, marts_validation
    quality         data_quality_report
    serving         published_files, pipeline_complete

A group-scoped job runs its own assets and loads upstream inputs from the last
materialisation. So the partial jobs below are RECOVERY tools -- they assume a
full run has happened before. Only daily_pipeline_job is safe from cold.

WHAT CHANGED
────────────
    ingestion_only_job   description said "-> seeds". There are no seeds; it
                         ends at landing_load, which writes the lake's landing
                         schema. Note what that means operationally: rows are
                         IN the lake but no bi.* view reflects them until
                         SQLMesh runs. Use it to debug extraction, not to
                         refresh reporting.
    quality_only_job     NEW. Re-running the audits used to mean rebuilding
                         marts, because data_quality_report was reachable only
                         through the full pipeline. Under the new architecture
                         the audits read marts and write landing.audit_*, so
                         they are independently re-runnable -- and they are the
                         thing you most often want to re-run after correcting
                         a workbook mapping.
    serving_only_job     description said "serving layer sync". There is no
                         sync: it publishes Parquet/CSV for Power BI and Excel.

NOTHING SELECTS A "seeds" GROUP because no asset declares one.
"""
from dagster import AssetSelection, define_asset_job

# The complete pipeline, in dependency order:
#   discovery -> preprocessing -> extract -> landing -> SQLMesh -> validate
#   -> quality -> publish
daily_pipeline_job = define_asset_job(  # pylint: disable=assignment-from-no-return
    name="daily_sales_pipeline",
    selection=AssetSelection.all(),
    description=(
        "Complete daily pipeline: Excel -> landing -> SQLMesh (marts/bi/meta) "
        "-> quality audits -> published files."
    ),
    tags={
        "team": "analytics",
        "domain": "sales",
        "sla": "daily_6am",
    },
)

# ── Partial jobs: recovery and debugging ───────────────────────────────────
# Each assumes a prior full run. They load upstream inputs from the last
# materialisation rather than rebuilding them.

ingestion_only_job = define_asset_job(  # pylint: disable=assignment-from-no-return
    name="ingestion_only",
    selection=AssetSelection.groups("ingestion"),
    description=(
        "Excel -> landing schema only. Stops before SQLMesh, so new rows are "
        "in the lake but not yet in any bi.* view. For debugging extraction."
    ),
)

transformation_only_job = define_asset_job(  # pylint: disable=assignment-from-no-return
    name="transformation_only",
    selection=AssetSelection.groups("transformation"),
    description=(
        "SQLMesh plan + audit, then validate marts, bi and meta. Rebuilds the "
        "serving layer from whatever is already in landing."
    ),
)

quality_only_job = define_asset_job(  # pylint: disable=assignment-from-no-return
    name="quality_only",
    selection=AssetSelection.groups("quality"),
    description=(
        "Re-run the data-quality audits against the current marts and append "
        "to landing.audit_results / landing.audit_failures. Does not rebuild "
        "marts. Use after fixing a reference workbook."
    ),
)

serving_only_job = define_asset_job(  # pylint: disable=assignment-from-no-return
    name="serving_only",
    selection=AssetSelection.groups("serving"),
    description=(
        "Publish Parquet (Power BI) and CSV (Excel) from the current lake. "
        "Interactive consumers attach the lake and need none of this."
    ),
)