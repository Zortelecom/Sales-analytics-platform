"""
Assets for SQLMesh transformations.

(2026-08) Two changes.

1. sqlmesh_models now depends on landing_load, not seeds_metadata. There are
   no seeds: ingestion writes to the landing schema of the lake and raw.* are
   views over it.

2. marts_validation validates marts, bi AND meta, and reads the catalog
   through DuckLakeResource instead of building its own connection. The
   previous version did `ATTACH 'ducklake:{path}'` correctly but the shared
   resource did not -- one of them had to be wrong, and duplicated connection
   logic is how they diverged. It also dropped its "find any schema containing
   mart" fallback: that silently validated marts__dev when asked for marts,
   which is the kind of helpfulness that hides a misconfigured environment.
"""
import time

from dagster import (
    AssetExecutionContext,
    AssetIn,
    Failure,
    MetadataValue,
    RetryPolicy,
    asset,
)

from orchestration.config import PipelineConfig
from orchestration.resources.duckdb_resource import DuckLakeResource
from orchestration.resources.sqlmesh_resource import SQLMeshResource
from orchestration.utils.constants import CATALOG_NAME, DUCKLAKE_PATH, schema_for

_cfg = PipelineConfig()


@asset(
    group_name="transformation",
    description="Runs SQLMesh plan and apply to transform seeds to marts",
    required_resource_keys={"sqlmesh"},
    compute_kind="sqlmesh",
    ins={"landing_load": AssetIn()},
    retry_policy=RetryPolicy(max_retries=2, delay=30)
)
def sqlmesh_models(context: AssetExecutionContext, landing_load: dict) -> dict:
    """
    Execute SQLMesh transformations to create marts tables.

    Process:
    1. Run SQLMesh plan (with auto-apply in dev environment)
    2. Execute audits/tests
    3. Verify models were created

    This transforms:
    - landing.* → raw.* views (current version of each source file)
    - raw → staging (typing, filtering, deduplication)
    - staging → marts (SCD2 dimensions, facts)
    - marts → bi (the semantic views every consumer reads)
    - landing.file_registry → meta (pipeline observability)

    Returns:
        Dictionary with transformation results
    """

    sqlmesh: SQLMeshResource = context.resources.sqlmesh

    context.log.info("="*70)
    context.log.info("Starting SQLMesh Transformation")
    context.log.info(f"  Environment: {sqlmesh.environment}")
    context.log.info(f"  Project:     {sqlmesh.project_path}")
    context.log.info(
        f"  Batch ID:    {landing_load.get('batch_id', 'unknown')}")
    context.log.info("="*70)

    try:
        # Run plan with auto-apply
        context.log.info("Running SQLMesh plan...")
        t0 = time.perf_counter()
        plan_result = sqlmesh.plan(
            context, start_date=_cfg.sqlmesh_start_date, auto_apply=True)

        # Log plan output (truncated for metadata)
        plan_output = plan_result.stdout if plan_result.stdout else "No output"
        context.log.info(
            f"Plan completed with return code: {plan_result.returncode}")

        # Run audits
        context.log.info("Running SQLMesh audits...")
        try:
            audit_result = sqlmesh.audit(context)
            audit_status = "passed" if audit_result.returncode == 0 else "failed"
            context.log.info(f"Audits {audit_status}")
        except Exception as audit_error:
            context.log.warning(f"Audits encountered an issue: {audit_error}")
            audit_status = "error"

        # Get model info for metadata
        try:
            model_info = sqlmesh.get_model_info()
            models_list = model_info.get("models", [])
        except Exception:
            context.log.warning("Could not retrieve model info")
            model_info = {}
            models_list = []

        duration = time.perf_counter() - t0

        # Verify DuckLake catalog was created
        ducklake_exists = DUCKLAKE_PATH.exists()
        ducklake_size_mb = 0
        if ducklake_exists:
            ducklake_size_mb = DUCKLAKE_PATH.stat().st_size / (1024 * 1024)

        context.add_output_metadata({
            "plan_output_preview": plan_output[:1000] if len(plan_output) > 1000 else plan_output,
            "audit_status": audit_status,
            "models_count": len(models_list),
            # Limit to 20 for display
            "models": models_list[:20] if models_list else [],
            "ducklake_path": str(DUCKLAKE_PATH),
            "ducklake_exists": ducklake_exists,
            "ducklake_size_mb": f"{ducklake_size_mb:.2f}",
            "batch_id": landing_load.get('batch_id', 'unknown'),
            "transformation_duration_seconds": round(duration, 2),
        })

        context.log.info("="*70)
        context.log.info("SQLMesh Transformation Complete")
        context.log.info(f"  Models processed: {len(models_list)}")
        context.log.info(f"  Audit status:     {audit_status}")
        context.log.info(f"  DuckLake catalog: {ducklake_size_mb:.2f} MB")
        context.log.info("="*70)

        return {
            "status": "success",
            "environment": sqlmesh.environment,
            "models_processed": models_list,
            "models_count": len(models_list),
            "audit_status": audit_status,
            "timestamp": str(context.run.run_id),
            "batch_id": landing_load.get('batch_id', 'unknown'),
        }

    except Exception as e:
        context.log.error(f"SQLMesh transformation failed: {e}", exc_info=True)
        raise


@asset(
    group_name="transformation",
    description="Validates that marts, bi and meta were built and hold data",
    compute_kind="duckdb",
    required_resource_keys={"ducklake"},
    ins={"sqlmesh_models": AssetIn()},
    retry_policy=RetryPolicy(max_retries=2, delay=10),
)
def marts_validation(context: AssetExecutionContext, sqlmesh_models: dict) -> dict:
    """
    Row counts for every object SQLMesh built, per schema.

    Validates all three serving schemas, not just marts: `bi` is what every
    consumer actually reads, and an empty bi view is invisible from marts
    alone. `meta` is checked too -- if it is empty the observability layer is
    reporting on nothing, which looks identical to a healthy quiet day.

    No "any schema containing 'mart'" fallback. That silently validated
    marts__dev when asked for marts, hiding a wrong SQLMESH_ENV behind a
    green tick.
    """
    ducklake: DuckLakeResource = context.resources.ducklake
    env = sqlmesh_models.get("environment", _cfg.sqlmesh_env)
    wanted = {logical: schema_for(logical, env) for logical in ("marts", "bi", "meta")}

    conn = ducklake.get_connection()
    try:
        present = {
            r[0] for r in conn.execute(
                "SELECT schema_name FROM duckdb_schemas() WHERE database_name = ?",
                [CATALOG_NAME],
            ).fetchall()
        }
        missing = {k: v for k, v in wanted.items() if v not in present}
        if "marts" in missing:
            raise Failure(
                f"Schema {wanted['marts']!r} not found. Present: {sorted(present)}. "
                f"Either `sqlmesh plan` did not run, or SQLMESH_ENV ({env!r}) "
                f"does not match the environment that was built."
            )
        for logical, name in missing.items():
            context.log.warning("Schema %s (%s) is absent", name, logical)

        counts: dict[str, int] = {}
        per_schema: dict[str, int] = {}

        for logical, schema in wanted.items():
            if schema in missing:
                continue
            objects = [
                r[0] for r in conn.execute(
                    "SELECT table_name FROM duckdb_tables() WHERE database_name = ? AND schema_name = ? "
                    "UNION ALL "
                    "SELECT view_name FROM duckdb_views() WHERE database_name = ? AND schema_name = ? "
                    "ORDER BY 1",
                    [CATALOG_NAME, schema, CATALOG_NAME, schema],
                ).fetchall()
                if not r[0].startswith("_")
            ]
            if not objects:
                raise Failure(f"Schema {schema!r} exists but contains no objects.")

            for obj in objects:
                try:
                    n = conn.execute(
                        f'SELECT COUNT(*) FROM "{CATALOG_NAME}"."{schema}"."{obj}"'
                    ).fetchone()[0]
                except Exception as exc:  # noqa: BLE001 -- one bad object, not the run
                    context.log.error("  %s.%s: %s", schema, obj, exc)
                    n = -1
                counts[f"{logical}.{obj}"] = n
                context.log.info("  %s %s.%s: %s rows",
                                 "OK " if n > 0 else "EMPTY", logical, obj,
                                 f"{n:,}" if n >= 0 else "error")
            per_schema[logical] = len(objects)
    finally:
        conn.close()

    empty = sorted(k for k, v in counts.items() if v == 0)
    errored = sorted(k for k, v in counts.items() if v < 0)
    total_rows = sum(v for v in counts.values() if v > 0)

    if errored:
        raise Failure(f"Could not read: {errored}")
    if not any(v > 0 for k, v in counts.items() if k.startswith("marts.")):
        raise Failure("Every marts object is empty.")

    context.add_output_metadata({
        "environment": env,
        "objects_by_schema": MetadataValue.json(per_schema),
        "total_rows": int(total_rows),
        "empty_objects": MetadataValue.json(empty),
        "ducklake_path": str(DUCKLAKE_PATH),
        "counts": MetadataValue.md(
            "\n".join(f"- `{k}`: {v:,} rows" for k, v in sorted(counts.items()))
        ),
    })

    if empty:
        # Not a failure: dim_product_price is legitimately empty until the
        # GMS and SD tiers are enabled in Ref_Products.
        context.log.warning("Empty objects: %s", empty)

    return counts