"""Daily ETL job definition - Dagster native (no subprocess)"""
from dagster import define_asset_job, AssetSelection

# The complete pipeline as a Dagster job
# This runs all assets in dependency order: discovery → preprocessing → ingestion → transformation → serving
daily_pipeline_job = define_asset_job(  # pylint: disable=assignment-from-no-return
    name="daily_sales_pipeline",
    selection=AssetSelection.all(),
    description="Complete daily pipeline: Discovery → Preprocessing → Ingestion → SQLMesh → Serving",
    tags={
        "team": "analytics",
        "domain": "sales",
        "sla": "daily_6am",
    }
)

# Optional: Partial jobs for specific operations (useful for testing/recovery)
ingestion_only_job = define_asset_job(  # pylint: disable=assignment-from-no-return
    name="ingestion_only",
    selection=AssetSelection.groups("ingestion"),
    description="Run only ingestion assets (discovery → preprocessing → seeds)",
)

transformation_only_job = define_asset_job(  # pylint: disable=assignment-from-no-return
    name="transformation_only",
    selection=AssetSelection.groups("transformation"),
    description="Run only SQLMesh transformation assets",
)

serving_only_job = define_asset_job(  # pylint: disable=assignment-from-no-return
    name="serving_only",
    selection=AssetSelection.groups("serving"),
    description="Run only serving layer sync",
)
