"""
Assets for SQLMesh transformations.

(2026-08) Aligned with the lake-native architecture and the verified SQLMesh
CLI surface.

1. sqlmesh_models depends on landing_load, not seeds_metadata. There are no
   seeds: ingestion writes to the landing schema of the lake and raw.* are
   views over it.

2. THE AUDIT BRANCH IS NOW REACHABLE. It read:

       audit_status = "passed" if audit_result.returncode == 0 else "failed"

   ...but _run_command raised Failure on any non-zero exit, so control never
   reached the else. Every real audit failure landed in the except block as
   audit_status="error" -- indistinguishable from a crash, or from the
   rejected `--environment` flag the resource used to pass. SQLMeshResource
   .audit() is non-fatal now, so all three outcomes are distinct:

       passed   audits ran and found nothing
       failed   audits ran and found something
       skipped  run_audits=False; plan already enforced them
       error    the audit process itself could not run

   Note what audit_status is NOT: a gate. Blocking audits fail `sqlmesh plan`,
   which is fatal and fails this asset. This value is reporting.

3. PROVENANCE IS BACKEND-AGNOSTIC. The asset stamped ducklake_path,
   ducklake_exists and ducklake_size_mb from the DuckDB FILE path on every
   run. Under a PostgreSQL catalog that path names a file no process opens, so
   every healthy run would report exists=False and 0.00 MB -- a provenance
   field that is wrong is worse than none, and this one is read by people
   trying to explain a number. serving/publish.py already deleted its
   equivalent for exactly this reason. lake.describe(role) renders both
   backends without a password.

4. marts_validation validates marts, bi AND meta, and reads the catalog
   through DuckLakeResource instead of building its own connection. It also
   dropped its "find any schema containing mart" fallback: that silently
   validated marts__dev when asked for marts, which is the kind of
   helpfulness that hides a misconfigured environment.
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
from orchestration.utils.constants import CATALOG_NAME, schema_for
from shared import lake

_cfg = PipelineConfig()


@asset(
    group_name="transformation",
    description="Runs SQLMesh plan/apply: landing -> raw -> staging -> marts -> bi/meta",
    required_resource_keys={"sqlmesh"},
    compute_kind="sqlmesh",
    ins={"landing_load": AssetIn()},
    retry_policy=RetryPolicy(max_retries=2, delay=30),
)
def sqlmesh_models(context: AssetExecutionContext, landing_load: dict) -> dict:
    """
    Execute SQLMesh transformations.

    - landing.*            -> raw.*      views over the CURRENT version of each file
    - raw.*                -> staging.*  typing, filtering, deduplication
    - staging.*            -> marts.*    SCD2 dimensions, facts
    - marts.*              -> bi.*       the semantic views every consumer reads
    - landing.file_registry-> meta.*     pipeline observability
    """
    sqlmesh: SQLMeshResource = context.resources.sqlmesh
    batch_id = landing_load.get("batch_id", "unknown")

    context.log.info("=" * 70)
    context.log.info("Starting SQLMesh transformation")
    context.log.info("  Environment: %s", sqlmesh.environment)
    context.log.info("  Project:     %s", sqlmesh.project_path)
    context.log.info("  Batch ID:    %s", batch_id)
    context.log.info("  Catalog:     %s", lake.describe("writer"))
    context.log.info("=" * 70)

    t0 = time.perf_counter()

    # Fatal on purpose: plan is where blocking audits are enforced.
    plan_result = sqlmesh.plan(
        context, start_date=_cfg.sqlmesh_start_date, auto_apply=True
    )
    plan_output = plan_result.stdout or "No output"

    # Non-fatal, and all four outcomes are now distinguishable.
    audit_output = ""
    try:
        audit_result = sqlmesh.audit(context)
        if audit_result is None:
            audit_status = "skipped"
        else:
            audit_status = "passed" if audit_result.returncode == 0 else "failed"
            audit_output = (audit_result.stdout or "")[-4000:]
    except Exception as audit_error:  # noqa: BLE001 -- the process, not the data
        context.log.warning("Audit process could not run: %s", audit_error)
        audit_status = "error"
        audit_output = str(audit_error)[:4000]

    try:
        project_info = sqlmesh.get_project_info(context)
    except Exception:  # noqa: BLE001
        context.log.warning("Could not retrieve project info")
        project_info = {}

    duration = time.perf_counter() - t0
    models_count = project_info.get("models")

    context.add_output_metadata({
        "environment": sqlmesh.environment,
        "catalog": lake.describe("writer"),
        "plan_output_preview": MetadataValue.text(plan_output[:2000]),
        "audit_status": audit_status,
        "audit_output": MetadataValue.text(audit_output) if audit_output else "none",
        "models_count": models_count if models_count is not None else "unparsed",
        "macros_count": project_info.get("macros") or "unparsed",
        "project_info": MetadataValue.text(project_info.get("raw", "unavailable")),
        "batch_id": batch_id,
        "transformation_duration_seconds": round(duration, 2),
    })

    context.log.info("=" * 70)
    context.log.info("SQLMesh transformation complete")
    context.log.info("  Models:       %s", models_count if models_count is not None else "?")
    context.log.info("  Audit status: %s", audit_status)
    context.log.info("  Duration:     %.1fs", duration)
    context.log.info("=" * 70)

    return {
        "status": "success",
        "environment": sqlmesh.environment,
        "models_count": models_count,
        "audit_status": audit_status,
        "run_id": context.run.run_id,
        "batch_id": batch_id,
    }


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

    Validates every schema SQLMesh builds for a consumer, not just marts:
    `bi` is what most consumers read and an empty bi view is invisible from
    marts alone; `reports` holds the pre-shaped rep_* views; `meta` is checked
    too -- if it is empty the observability layer is reporting on nothing,
    which looks identical to a healthy quiet day.

    Only `marts` being absent is fatal. A missing reports or meta schema warns,
    because those can legitimately not exist yet in a fresh environment while
    marts cannot.

    No "any schema containing 'mart'" fallback. That silently validated
    marts__dev when asked for marts, hiding a wrong SQLMESH_ENV behind a
    green tick.
    """
    ducklake: DuckLakeResource = context.resources.ducklake
    env = sqlmesh_models.get("environment", _cfg.sqlmesh_env)
    # reports joins marts/bi/meta. It was absent, so rep_target_attainment,
    # rep_weekly_meeting and rep_top_products were built by every plan and
    # checked by nothing -- an empty report view looked identical to a healthy
    # one. meta stays in the list but is never published; it is observability.
    wanted = {
        logical: schema_for(logical, env)
        for logical in ("marts", "bi", "reports", "meta")
    }

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
                f"Schema {wanted['marts']!r} not found in catalog {CATALOG_NAME!r}. "
                f"Present: {sorted(present)}. Either `sqlmesh plan` did not run, "
                f"or SQLMESH_ENV ({env!r}) does not match the environment that "
                f"was built."
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
                context.log.info(
                    "  %s %s.%s: %s rows",
                    "OK " if n > 0 else "EMPTY", logical, obj,
                    f"{n:,}" if n >= 0 else "error",
                )
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
        "catalog": lake.describe("reader"),
        "objects_by_schema": MetadataValue.json(per_schema),
        "total_rows": int(total_rows),
        "empty_objects": MetadataValue.json(empty),
        "counts": MetadataValue.md(
            "\n".join(f"- `{k}`: {v:,} rows" for k, v in sorted(counts.items()))
        ),
    })

    if empty:
        # Not a failure: dim_product_price is legitimately empty until the
        # GMS and SD tiers are enabled in Ref_Products.
        context.log.warning("Empty objects: %s", empty)

    return counts