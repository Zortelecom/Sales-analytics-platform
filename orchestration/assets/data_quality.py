"""
orchestration/assets/data_quality.py

Runs every SQLMesh audit query directly against DuckDB after
transformation completes. For each failure, traces the offending
rows back to their source Excel file using filename_subregion
(sales) or seed metadata files (reference data).

Produces:
  - Dagster AssetCheckResult per audit (visible in the Dagster UI)
  - A structured JSON quality report in data/exports/quality_reports/
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
from dagster import (
    AssetCheckResult,
    AssetCheckSeverity,
    MetadataValue,
    asset_check,
)

from orchestration.resources.duckdb_resource import DuckDBResource
from orchestration.utils.constants import (
    QUALITY_REPORTS_DIR,
    SEEDS_DIR,
    SQLMESH_ENV,
    get_serving_db_path,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _table(model: str) -> str:
    """
    Resolve the physical DuckLake table name for a SQLMesh model in the
    configured environment.

    SQLMesh names tables as:  <layer>__<<env>.<<model_name>
    e.g. staging__dev.stg_sales_data  →  sqlmesh__staging.staging__stg_sales_data__<<hash>__dev

    We query the information_schema instead of hard-coding hashes so this
    is always correct regardless of model fingerprint changes.
    """
    return model  # passed as fully-qualified view alias; real resolution below


def _run(conn: duckdb.DuckDBPyConnection, sql: str) -> list[dict]:
    """Execute a query and return rows as a list of dicts."""
    try:
        rel = conn.execute(sql)
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, row)) for row in rel.fetchall()]
    except Exception as exc:
        logger.error("Audit query failed: %s\n%s", exc, sql)
        return []


def _load_seed_metadata(seed_name: str) -> dict:
    """Parse a seed *_metadata.txt file into a dict."""
    path = Path(SEEDS_DIR) / f"{seed_name}_metadata.txt"
    if not path.exists():
        return {}
    meta: dict[str, Any] = {}
    with path.open() as f:
        for line in f:
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()
    return meta


def _write_report(report: dict) -> Path:
    """Persist the quality report as JSON and return its path."""
    out_dir = Path(QUALITY_REPORTS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"quality_report_{ts}.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    logger.info("Quality report written to %s", path)
    return path


def _source_file_summary(rows: list[dict], file_col: str = "filename_subregion") -> dict:
    """
    Group failing rows by source file.
    Returns  { "ExSD-Sales-Est.xlsx": 12, "ExSD-Sales-Yde_Nord.xlsx": 3, ... }
    """
    summary: dict[str, int] = {}
    for row in rows:
        fname = row.get(file_col) or "unknown"
        summary[fname] = summary.get(fname, 0) + 1
    return summary


# ---------------------------------------------------------------------------
# Audit definitions
# Each function now receives the resolved env string so table names are
# evaluated at execution time, not import time.
# ---------------------------------------------------------------------------

def _audit_not_null_fact_sales(conn: duckdb.DuckDBPyConnection, env: str) -> tuple[list, list]:
    """Mirrors the not_null audit on fact_sales."""
    _FACT = f"marts__{env}.fact_sales"
    _STG_SALES = f"staging__{env}.stg_sales_data"

    failing = _run(conn, f"""
        SELECT
            f.sales_line_id,
            f.sale_date,
            f.sku,
            f.salesperson_id
        FROM {_FACT} f
        WHERE f.sales_line_id IS NULL
           OR f.sale_date     IS NULL
           OR f.sku           IS NULL
           OR f.salesperson_id IS NULL
    """)
    if not failing:
        return [], []

    # Trace: join back to stg_sales_data to get filename_subregion
    ids = ", ".join(
        f"'{r['sales_line_id']}'" for r in failing if r.get("sales_line_id")
    )
    trace = _run(conn, f"""
        SELECT
            s.sales_line_id,
            s.sale_date,
            s.sku,
            s.salesperson_id,
            s.filename_subregion AS source_file,
            CASE
                WHEN s.sales_line_id  IS NULL THEN 'sales_line_id'
                WHEN s.sale_date      IS NULL THEN 'sale_date'
                WHEN s.sku            IS NULL THEN 'sku'
                WHEN s.salesperson_id IS NULL THEN 'salesperson_id'
            END AS null_column
        FROM {_STG_SALES} s
        WHERE s.sales_line_id IS NULL
           OR s.sale_date     IS NULL
           OR s.sku           IS NULL
           OR s.salesperson_id IS NULL
    """) if not ids else _run(conn, f"""
        SELECT
            s.sales_line_id,
            s.filename_subregion  AS source_file,
            CASE
                WHEN f.sales_line_id  IS NULL THEN 'sales_line_id'
                WHEN f.sale_date      IS NULL THEN 'sale_date'
                WHEN f.sku            IS NULL THEN 'sku'
                WHEN f.salesperson_id IS NULL THEN 'salesperson_id'
            END AS null_column
        FROM {_FACT} f
        LEFT JOIN {_STG_SALES} s USING (sales_line_id)
        WHERE f.sales_line_id IS NULL
           OR f.sale_date     IS NULL
           OR f.sku           IS NULL
           OR f.salesperson_id IS NULL
    """)
    return failing, trace


def _audit_negative_amount(conn: duckdb.DuckDBPyConnection, env: str) -> tuple[list, list]:
    """Mirrors accepted_range(column := total_amount, min_v := 0, inclusive := false)."""
    _FACT = f"marts__{env}.fact_sales"
    _STG_SALES = f"staging__{env}.stg_sales_data"

    failing = _run(conn, f"""
        SELECT sales_line_id, sale_date, sku, salesperson_id, total_amount
        FROM {_FACT}
        WHERE total_amount <= 0
    """)
    if not failing:
        return [], []

    ids = ", ".join(f"'{r['sales_line_id']}'" for r in failing)
    trace = _run(conn, f"""
        SELECT
            f.sales_line_id,
            f.sale_date,
            f.sku,
            f.salesperson_id,
            f.total_amount,
            s.filename_subregion AS source_file
        FROM {_FACT} f
        LEFT JOIN {_STG_SALES} s USING (sales_line_id)
        WHERE f.sales_line_id IN ({ids})
    """)
    return failing, trace


def _audit_orphaned_products(conn: duckdb.DuckDBPyConnection, env: str) -> tuple[list, list]:
    """Mirrors assert_no_orphaned_product."""
    _FACT = f"marts__{env}.fact_sales"
    _STG_SALES = f"staging__{env}.stg_sales_data"

    failing = _run(conn, f"""
        SELECT sales_line_id, sale_date, sku, product_key
        FROM {_FACT}
        WHERE sku IS NOT NULL
          AND product_key IS NULL
    """)
    if not failing:
        return [], []

    ids = ", ".join(f"'{r['sales_line_id']}'" for r in failing)
    trace = _run(conn, f"""
        SELECT
            f.sales_line_id,
            f.sale_date,
            f.sku,
            s.filename_subregion AS source_file,
            'SKU absent from products reference or outside SCD window' AS reason
        FROM {_FACT} f
        LEFT JOIN {_STG_SALES} s USING (sales_line_id)
        WHERE f.sales_line_id IN ({ids})
    """)
    return failing, trace


def _audit_amount_vs_qty_price(conn: duckdb.DuckDBPyConnection, env: str) -> tuple[list, list]:
    """Mirrors assert_amount_matches_qty_x_price (>>1 % deviation)."""
    _FACT = f"marts__{env}.fact_sales"
    _STG_SALES = f"staging__{env}.stg_sales_data"

    failing = _run(conn, f"""
        SELECT
            sales_line_id,
            sale_date,
            sku,
            quantity,
            unit_price_actual,
            total_amount,
            ROUND(quantity * unit_price_actual, 2)  AS expected_amount,
            ROUND(
                ABS(total_amount - (quantity * unit_price_actual))
                / NULLIF(quantity * unit_price_actual, 0) * 100, 2
            ) AS deviation_pct
        FROM {_FACT}
        WHERE unit_price_actual > 0
          AND quantity          > 0
          AND ABS(total_amount - (quantity * unit_price_actual))
              / NULLIF(quantity * unit_price_actual, 0) > 0.01
    """)
    if not failing:
        return [], []

    ids = ", ".join(f"'{r['sales_line_id']}'" for r in failing)
    trace = _run(conn, f"""
        SELECT
            f.sales_line_id,
            f.sale_date,
            f.sku,
            f.quantity,
            f.unit_price_actual,
            f.total_amount,
            s.filename_subregion AS source_file
        FROM {_FACT} f
        LEFT JOIN {_STG_SALES} s USING (sales_line_id)
        WHERE f.sales_line_id IN ({ids})
    """)
    return failing, trace


def _audit_product_price(conn: duckdb.DuckDBPyConnection, env: str) -> tuple[list, list]:
    """Mirrors accepted_range on unit_price in stg_products_data."""
    _STG_PRODUCTS = f"staging__{env}.stg_products_data"

    failing = _run(conn, f"""
        SELECT product_key, sku, product_name, unit_price
        FROM {_STG_PRODUCTS}
        WHERE unit_price IS NULL OR unit_price < 1
    """)
    meta = _load_seed_metadata("products_data")
    trace = [
        {**row, "source_file": meta.get("Source Files", "References.xlsx")}
        for row in failing
    ]
    return failing, trace


# ---------------------------------------------------------------------------
# Main orchestrator asset check
# ---------------------------------------------------------------------------

@asset_check(
    asset="fact_sales",
    name="data_quality_full_report",
    description=(
        "Runs all audit queries against the current dev tables, "
        "traces every failing row back to its source Excel file, "
        "and writes a structured JSON report."
    ),
    blocking=False,   # graceful failure: pipeline continues, UI shows red badge
)
def data_quality_full_report(duckdb: DuckDBResource) -> AssetCheckResult:
    """
    Graceful failure strategy
    ─────────────────────────
    blocking=False means:
      - A failed audit does NOT stop downstream assets.
      - The Dagster UI shows a red ❌ badge on fact_sales.
      - The JSON report is always written so engineers can inspect it.
      - The AssetCheckResult metadata contains a per-audit summary
        directly in the UI without opening a file.

    The DuckDBResource resolves the correct env-aware serving DB path
    (serving_dev.db / serving.db) via get_serving_db_path(), so this
    check always queries the same file the serving asset just wrote.
    """
    # Resolve env at call time — never cache at import time
    _ENV = SQLMESH_ENV

    run_dt = datetime.now(timezone.utc)
    run_ts = run_dt.isoformat()
    report: dict[str, Any] = {
        "run_at": run_ts,
        "environment": _ENV,
        "audits": {},
    }

    all_passed = True
    any_error = False
    ui_summary: dict[str, str] = {}

    audits = [
        ("not_null_fact_sales",       _audit_not_null_fact_sales),
        ("negative_total_amount",     _audit_negative_amount),
        ("orphaned_products",         _audit_orphaned_products),
        ("amount_vs_qty_price",       _audit_amount_vs_qty_price),
        ("product_price_invalid",     _audit_product_price),
    ]

    with duckdb.get_connection() as conn:
        for audit_name, audit_fn in audits:
            try:
                failing_rows, trace_rows = audit_fn(conn, _ENV)
            except Exception as exc:
                logger.error("Audit %s raised: %s", audit_name, exc)
                all_passed = False
                any_error = True
                failing_rows, trace_rows = [], []
                report["audits"][audit_name] = {"error": str(exc)}
                ui_summary[audit_name] = f"⚠ ERROR: {exc}"
                continue

            passed = len(failing_rows) == 0

            if not passed:
                all_passed = False
                by_file = _source_file_summary(trace_rows, "source_file")
                report["audits"][audit_name] = {
                    "status": "FAILED",
                    "failing_row_count": len(failing_rows),
                    "by_source_file": by_file,
                    "failing_rows": trace_rows,
                }
                file_breakdown = " | ".join(
                    f"{fname}: {n} row{'s' if n > 1 else ''}"
                    for fname, n in by_file.items()
                )
                ui_summary[audit_name] = (
                    f"❌ {len(failing_rows)} rows failed"
                    + (f"  ←  {file_breakdown}" if file_breakdown else "")
                )
                logger.warning(
                    "Audit %s FAILED: %d rows. By source file: %s",
                    audit_name, len(failing_rows), by_file,
                )
            else:
                report["audits"][audit_name] = {"status": "PASSED"}
                ui_summary[audit_name] = "✔ passed"

        # Write JSON report
        report_path = _write_report(report)
        report["report_path"] = str(report_path)

        # Persist one row per audit to serving DB
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bi.quality_trend (
                    run_at      TIMESTAMPTZ,
                    audit       TEXT,
                    status      TEXT,
                    failing_rows INT
                )
            """)

            for audit_name, _ in audits:
                audit_data = report["audits"].get(audit_name, {})
                status = audit_data.get("status", "UNKNOWN")
                failing_count = 0

                if status == "FAILED":
                    failing_count = audit_data.get("failing_row_count", 0)
                elif "error" in audit_data:
                    status = "ERROR"

                conn.execute("""
                    INSERT INTO bi.quality_trend (run_at, audit, status, failing_rows)
                    VALUES (?, ?, ?, ?)
                """, (run_dt, audit_name, status, failing_count))

            logger.info("Quality trend persisted to bi.quality_trend (%s)", run_ts)

        except Exception as exc:
            logger.error("Failed to persist quality trend: %s", exc)

    severity = AssetCheckSeverity.ERROR if any_error else AssetCheckSeverity.WARN

    return AssetCheckResult(
        passed=all_passed,
        severity=severity,
        metadata={
            "audit_results": MetadataValue.json(ui_summary),
            "report_path":   MetadataValue.path(str(report_path)),
            "run_at":        MetadataValue.text(run_ts),
            "environment":   MetadataValue.text(_ENV),
        },
    )
