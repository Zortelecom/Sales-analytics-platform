"""Asset for SQLMesh transformations"""
from dagster import Failure, asset, MetadataValue, AssetExecutionContext, AssetIn, RetryPolicy
import pandas as pd
import duckdb

from orchestration.resources.sqlmesh_resource import SQLMeshResource
from orchestration.utils.constants import DUCKLAKE_PATH, CATALOG_NAME


@asset(
    group_name="transformation",
    description="Runs SQLMesh plan and apply to transform seeds to marts",
    required_resource_keys={"sqlmesh"},
    compute_kind="sqlmesh",
    ins={"seeds_metadata": AssetIn()},
    retry_policy=RetryPolicy(max_retries=2, delay=30)
)
def sqlmesh_models(context: AssetExecutionContext, seeds_metadata: dict) -> dict:
    """
    Execute SQLMesh transformations to create marts tables.

    Process:
    1. Run SQLMesh plan (with auto-apply in dev environment)
    2. Execute audits/tests
    3. Verify models were created

    This transforms:
    - Seeds → Raw models (Bronze layer)
    - Raw → Staging models (Silver layer)
    - Staging → Marts models (Gold layer)

    Returns:
        Dictionary with transformation results
    """

    sqlmesh: SQLMeshResource = context.resources.sqlmesh

    context.log.info("="*70)
    context.log.info("Starting SQLMesh Transformation")
    context.log.info(f"  Environment: {sqlmesh.environment}")
    context.log.info(f"  Project:     {sqlmesh.project_path}")
    context.log.info(
        f"  Batch ID:    {seeds_metadata.get('batch_id', 'unknown')}")
    context.log.info("="*70)

    try:
        # Run plan with auto-apply
        context.log.info("Running SQLMesh plan...")
        plan_result = sqlmesh.plan(
            context, start_date='2025-01-01', auto_apply=True)

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
            "batch_id": seeds_metadata.get('batch_id', 'unknown'),
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
            "batch_id": seeds_metadata.get('batch_id', 'unknown'),
        }

    except Exception as e:
        context.log.error(f"SQLMesh transformation failed: {e}", exc_info=True)
        raise


@asset(
    group_name="transformation",
    description="Validates marts tables exist and have data by querying DuckLake catalog",
    compute_kind="duckdb",
    ins={"sqlmesh_models": AssetIn()},
    retry_policy=RetryPolicy(max_retries=2, delay=10)
)
def marts_validation(context: AssetExecutionContext, sqlmesh_models: dict) -> pd.DataFrame:
    """
    Validate that marts tables were created successfully in DuckLake.
    
    Uses robust discovery logic from test.py:
    1. Explicitly loads DuckLake extension
    2. Dynamically searches for the schema (doesn't assume exact name)
    3. Validates row counts and columns
    """

    context.log.info("Validating marts tables in DuckLake...")

    if not DUCKLAKE_PATH.exists():
        raise Failure(f"DuckLake catalog not found: {DUCKLAKE_PATH}")

    # Determine target schema name based on environment (as a starting point)
    environment = sqlmesh_models.get("environment", "dev")
    expected_schema = "marts" if environment == "prod" else f"marts__{environment}"

    conn = None
    try:
        # 1. SETUP CONNECTION
        conn = duckdb.connect(":memory:")

        # Install/Load DuckLake extension (Explicit logic from test.py)
        try:
            conn.execute("LOAD ducklake;")
            context.log.info("DuckLake extension loaded")
        except duckdb.CatalogException:
            context.log.info("Installing DuckLake extension...")
            conn.execute("INSTALL ducklake; LOAD ducklake;")

        # 2. ATTACH CATALOG
        # Using the robust 'ducklake:' protocol syntax
        attach_sql = f"ATTACH 'ducklake:{DUCKLAKE_PATH}' AS {CATALOG_NAME};"
        context.log.info(f"Executing: {attach_sql}")
        conn.execute(attach_sql)

        # Switch context to the catalog
        conn.execute(f"USE {CATALOG_NAME};")

        # 3. DYNAMIC SCHEMA DISCOVERY (The key fix from test.py)
        schemas_df = conn.execute("""
            SELECT DISTINCT table_schema 
            FROM information_schema.tables 
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog', 'main')
            ORDER BY table_schema
        """).fetchdf()

        available_schemas = schemas_df['table_schema'].tolist(
        ) if not schemas_df.empty else []
        context.log.info(f"Available schemas in catalog: {available_schemas}")

        # Smart Schema Matching
        actual_schema = None
        if expected_schema in available_schemas:
            actual_schema = expected_schema
        else:
            # Fallback: search for any schema containing "mart"
            for schema in available_schemas:
                if "mart" in schema.lower():
                    actual_schema = schema
                    context.log.warning(
                        f"Expected schema '{expected_schema}' not found. "
                        f"Found and using '{schema}' instead."
                    )
                    break

        if not actual_schema:
            raise Failure(
                f"No 'marts' schema found. Available schemas: {available_schemas}"
            )

        # 4. FETCH TABLES
        tables_df = conn.execute("""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema = ?
              -- AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """, [actual_schema]).fetchdf()

        if tables_df.empty:
            raise Failure(f"No tables found in schema '{actual_schema}'")

        context.log.info(
            f"Found {len(tables_df)} table(s) in '{actual_schema}'")

        # 5. VALIDATE CONTENT
        validation_results = []

        for _, row in tables_df.iterrows():
            schema = row['table_schema']
            table = row['table_name']

            # Use quoted identifiers for safety
            full_name = f'"{CATALOG_NAME}"."{schema}"."{table}"'

            try:
                # Count rows
                count_result = conn.execute(
                    f"SELECT COUNT(*) FROM {full_name}").fetchone()
                row_count = count_result[0] if count_result else 0

                # Count columns
                columns_df = conn.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_schema = ? AND table_name = ?
                """, [schema, table]).fetchdf()
                column_count = len(columns_df)

                # Determine status
                status = "valid" if row_count > 0 and column_count > 0 else "warning"
                status_icon = "✅" if status == "valid" else "⚠️"

                validation_results.append({
                    "table_schema": schema,
                    "table_name": table,
                    "row_count": row_count,
                    "column_count": column_count,
                    "status": status,
                })

                context.log.info(
                    f"  {status_icon} {table}: {row_count:,} rows, {column_count} cols"
                )

            except Exception as table_error:
                context.log.error(
                    f"  ❌ Error validating {table}: {table_error}")
                validation_results.append({
                    "table_schema": schema,
                    "table_name": table,
                    "row_count": -1,
                    "column_count": -1,
                    "status": "error",
                    "error": str(table_error),
                })

        result_df = pd.DataFrame(validation_results)

        # 6. REPORTING
        valid_tables = len(result_df[result_df["status"] == "valid"])
        total_rows = result_df[result_df["row_count"] > 0]["row_count"].sum()

        context.add_output_metadata({
            "row_count": len(result_df),
            "valid_tables": valid_tables,
            "total_rows": int(total_rows),
            "marts_schema": actual_schema,
            "ducklake_path": str(DUCKLAKE_PATH),
            "preview": MetadataValue.md(
                result_df[["table_name", "row_count",
                           "column_count", "status"]].to_markdown()
            ),
        })

        if valid_tables == 0:
            raise Failure("No valid marts tables found (all empty or errors)")

        return result_df

    except Failure:
        raise
    except Exception as e:
        context.log.error(f"Validation failed: {e}", exc_info=True)
        raise Failure(f"Marts validation failed: {e}") from e
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
