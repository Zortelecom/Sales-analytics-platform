"""Asset for serving layer sync"""

import sys
from pathlib import Path
from datetime import datetime
import pandas as pd
from dagster import asset, AssetExecutionContext, Failure, AssetIn
from serving.sync import ServingLayerSync
from serving.config import ServingConfig


# ✅ FIX: Add project root to path
sys.path.append(str(Path(__file__).parent.parent.parent))


@asset(
    group_name="serving",
    description="Syncs marts tables from DuckLake to serving database for BI",
    compute_kind="python",
    ins={"marts_validation": AssetIn()}
)
def serving_database(context: AssetExecutionContext, marts_validation: pd.DataFrame) -> dict:
    """Sync DuckLake marts to serving DB"""

    # ✅ FIX: Let normalize() handle paths
    config = ServingConfig(environment="dev")
    config.normalize(project_root=Path(__file__).parent.parent.parent)

    sync = ServingLayerSync(config)

    try:
        # Run sync
        sync.sync(dry_run=False)

        # Validate
        is_valid = sync.validate_serving_db()

        # Get file stats
        serving_path = Path(config.serving_path)
        size_mb = serving_path.stat().st_size / (1024 * 1024) if serving_path.exists() else 0

        context.add_output_metadata({
            "serving_path": config.serving_path,
            "serving_exists": serving_path.exists(),
            "validation_passed": is_valid,
            "size_mb": size_mb,
            "environment": config.environment,
            "marts_schema": config.marts_schema,
        })

        return {
            "status": "success",
            "serving_path": config.serving_path,
            "validated": is_valid,
        }

    except Exception as e:
        raise Failure(description=f"Serving layer sync failed: {e}") from e


@asset(
    group_name="serving",
    description="Final pipeline completion marker",
    ins={"serving_database": AssetIn()}
)
def pipeline_complete(context: AssetExecutionContext, serving_database: dict) -> dict:
    """Pipeline completion marker with summary"""

    summary = {
        "timestamp": datetime.now().isoformat(),
        "run_id": context.run.run_id,
        "data_freshness": "current",
        "serving_db": serving_database["serving_path"],
        "available_for_bi": serving_database["validated"],
    }

    context.add_output_metadata({
        "pipeline_status": "completed",
        "summary": summary,
    })

    context.log.info("🎉 Pipeline completed successfully!")
    return summary
