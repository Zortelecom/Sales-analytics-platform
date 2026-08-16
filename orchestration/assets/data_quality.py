"""
orchestration/assets/data_quality.py

Persisted, traceable data-quality results — the half SQLMesh audits cannot do.

WHY THIS EXISTS ALONGSIDE SQLMESH AUDITS
────────────────────────────────────────
SQLMesh audits enforce rules at build time and report inline on the console.
That is the right place to stop bad data, and it is useless to a supervisor:
turning "12 rows failed" into "which workbook, whose tab" means running
`sqlmesh fetchdf` with a hand-written query. The people who have to FIX the
data are not SQL users.

So these checks answer a different question. Not "should the build proceed"
(SQLMesh owns that) but "who needs to open which file". They:

  * run after the marts are built, against the same lake
  * write a SUMMARY row per audit per run, so failures can be trended
  * write the FAILING ROWS with source_file / sheet_name / source_row_num, so
    a page can group by workbook and a supervisor can be handed a filename
  * never block: blocking is SQLMesh's job

The overlap with SQLMesh audits is deliberate and narrow. Where a rule exists
in both, SQLMesh's is authoritative for stopping the build; this one exists to
attribute it. Keep the SQL here aligned with sqlmesh/audits/ — a rule that
disagrees between the two is worse than a rule enforced once.

WHY AN ASSET PLUS A CHECK, NOT JUST A CHECK
───────────────────────────────────────────
Writing the results and reporting the verdict are split:

    data_quality_report   ASSET  — opens the lake for WRITING, runs the audits,
                                   appends results. Sits IN the dependency
                                   chain, between marts_validation and
                                   published_files.
    data_quality_checks   CHECK  — opens the lake READ-ONLY, reads what the
                                   asset just wrote, reports pass/fail.

It was one asset check, and that deadlocked: Dagster runs checks in PARALLEL
with downstream assets by design, so the check held a write attach while
published_files tried to read the same catalog —

    IO Error: Failed to attach DuckLake MetaData ...
    File is already open in python.exe (PID 19232)

Making the check blocking would have ordered it correctly and also failed the
whole pipeline every time negative_sellin found a return, which is the exact
opposite of what these audits are for. An asset check is the wrong shape for
something that takes an exclusive lock; an asset in the chain is the right one.

The check is now read-only, so it can run concurrently with anything.

WHERE RESULTS GO
────────────────
landing.audit_results and landing.audit_failures, appended.

The `landing` schema, not `meta`: meta.* are SQLMesh VIEW models and SQLMesh
owns that schema — writing tables into it invites a plan to drop them. This
mirrors landing.file_registry, which is written by ingestion and exposed
through meta.ingestion_batches. The matching views are
sqlmesh/models/meta/meta_audit_*.sql.
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

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import duckdb
from dagster import (
    AssetCheckExecutionContext,
    AssetCheckResult,
    AssetCheckSeverity,
    AssetExecutionContext,
    AssetIn,
    MetadataValue,
    asset,
    asset_check,
)

from orchestration.config import PipelineConfig
from orchestration.resources.duckdb_resource import CATALOG_ALIAS, DuckLakeResource
from ingestion.config.landing import LANDING_DDL
from orchestration.utils.constants import schema_for

logger = logging.getLogger(__name__)
_cfg = PipelineConfig()

# Columns every audit query must project, so results are uniformly traceable.
TRACE_COLUMNS = ("source_file", "sheet_name", "source_row_num")


@dataclass(frozen=True)
class Audit:
    """
    One data-quality question.

    `where` is a predicate over the fact table. Keeping it a predicate rather
    than a full query is what lets every audit project the same trace columns
    without each one repeating them -- and stops a new audit forgetting to.
    """
    name: str
    entity: str                 # "fact_sales" | "fact_kp_sd"
    question: str               # plain language, shown in the UI
    where: str
    severity: str = "warn"      # "warn" | "error" -- display only, never blocks
    columns: tuple = ()         # extra business columns to keep on failing rows


SALES_AUDITS: List[Audit] = [
    Audit(
        name="orphaned_product",
        entity="fact_sales",
        question="Sales lines whose SKU is not in the product reference",
        where="sku IS NOT NULL AND product_key IS NULL",
        severity="error",
        columns=("sku", "sale_date", "salesperson_id", "total_amount"),
    ),
    Audit(
        name="orphaned_salesperson",
        entity="fact_sales",
        question="Sales lines whose salesperson is not in the team reference",
        where="salesperson_id IS NOT NULL AND salesperson_key IS NULL",
        severity="error",
        columns=("salesperson_id", "sale_date", "total_amount"),
    ),
    Audit(
        name="orphaned_client",
        entity="fact_sales",
        question="Sales lines whose sub-distributor is not in the SD reference",
        where="clientsd_id IS NOT NULL AND clientsd_key IS NULL",
        severity="error",
        columns=("clientsd_id", "sale_date", "total_amount"),
    ),
    Audit(
        name="zero_or_negative_line",
        entity="fact_sales",
        # GMS accepts returns; traditional trade does not. Mirrors
        # sqlmesh/audits/assert_line_signs_are_coherent.sql.
        question="Zero lines, sign mismatches, or a return outside GMS",
        where=(
            "quantity = 0 OR total_amount = 0 "
            "OR (price_tier <> 'GMS' AND (quantity < 0 OR total_amount < 0)) "
            "OR SIGN(quantity) <> SIGN(total_amount)"
        ),
        severity="error",
        columns=("sku", "sale_date", "quantity", "total_amount", "price_tier"),
    ),
    Audit(
        name="price_off_reference",
        entity="fact_sales",
        question="Lines priced more than 5% away from the reference for their channel",
        where=(
            "quantity > 0 AND total_amount > 0 AND ("
            "  unit_price_standard IS NULL"
            "  OR ABS(unit_price_effective - unit_price_standard)"
            "     / NULLIF(unit_price_standard, 0) * 100 > 5"
            ")"
        ),
        columns=("sku", "sale_date", "price_tier", "unit_price_effective",
                 "unit_price_standard", "price_variance_pct"),
    ),
    Audit(
        name="sheet_lookup_out_of_date",
        entity="fact_sales",
        question="Workbook price differs from the price in force on the sale date",
        where=(
            "price_tier = 'TT' AND unit_price_sheet > 0 AND unit_price_standard > 0 "
            "AND ABS(unit_price_sheet - unit_price_standard)"
            "    / NULLIF(unit_price_standard, 0) * 100 > 0.5"
        ),
        columns=("sku", "sale_date", "unit_price_sheet", "unit_price_standard"),
    ),
]

KP_SD_AUDITS: List[Audit] = [
    Audit(
        name="orphaned_product_kp",
        entity="fact_kp_sd",
        question="Sell-in lines whose SKU is not in the product reference",
        where="sku IS NOT NULL AND product_key IS NULL",
        severity="error",
        columns=("sku", "sale_date", "clientsd_id", "total_amount"),
    ),
    Audit(
        name="orphaned_client_kp",
        entity="fact_kp_sd",
        question="Sell-in lines whose sub-distributor is not in the SD reference",
        where="clientsd_id IS NOT NULL AND clientsd_key IS NULL",
        severity="error",
        columns=("clientsd_id", "sale_date", "kp_name", "total_amount"),
    ),
    Audit(
        name="zero_or_sign_mismatch_kp",
        entity="fact_kp_sd",
        # Negatives are legitimate here -- an SD returns stock to a KP -- so
        # only zero lines and sign mismatches. See
        # sqlmesh/audits/assert_line_signs_are_coherent_kp.sql.
        question="Zero sell-in lines, or a negative quantity with a positive amount",
        where=(
            "quantity = 0 OR total_amount = 0 "
            "OR SIGN(quantity) <> SIGN(total_amount)"
        ),
        severity="error",
        columns=("sku", "sale_date", "clientsd_id", "quantity", "total_amount"),
    ),
    Audit(
        name="destockage_channel_conflict",
        entity="fact_kp_sd",
        question="Line filed in a workbook that contradicts the SD's destockage status",
        where="destockage_channel_conflict",
        columns=("clientsd_id", "sale_date", "destockage_channel",
                 "is_destocked_sd", "total_amount"),
    ),
    Audit(
        name="kp_mismatch",
        entity="fact_kp_sd",
        question="Key Player on the line differs from the SD's KP of record",
        where="kp_mismatch",
        columns=("clientsd_id", "kp_name", "kp_of_record_sd", "sale_date", "total_amount"),
    ),
    Audit(
        name="negative_sellin",
        entity="fact_kp_sd",
        question="Returns from a sub-distributor to a Key Player",
        where="quantity < 0 OR total_amount < 0",
        columns=("clientsd_id", "kp_name", "sku", "sale_date", "quantity", "total_amount"),
    ),
]

MAX_FAILING_ROWS_STORED = 500
"""Per audit per run. A rule failing on 30,000 rows is a broken rule, not
30,000 problems -- storing them all would bloat the lake and tell a supervisor
nothing they cannot see from the count and a sample."""

# DDL is shared with ingestion/config/landing.py: the tables are created empty
# when the landing schema is created, because `sqlmesh plan` builds views over
# them and would otherwise fail before this check could ever run. Re-applied
# here (CREATE IF NOT EXISTS) so this asset also works against a lake that
# predates that change.


def _run_audits(
    conn: duckdb.DuckDBPyConnection,
    audits: List[Audit],
    env: str,
    run_id: str,
    context,
) -> dict:
    marts = schema_for("marts", env)
    run_at = datetime.now(timezone.utc).replace(tzinfo=None)

    conn.execute("CREATE SCHEMA IF NOT EXISTS landing")
    for ddl in LANDING_DDL:
        conn.execute(ddl.format(schema="landing"))

    summary: dict = {}
    total_failing = 0
    worst = "PASSED"

    for audit in audits:
        table = f'"{CATALOG_ALIAS}"."{marts}"."{audit.entity}"'
        trace = ", ".join(TRACE_COLUMNS)
        # struct_pack requires NAMED arguments (a := b), not 'key', value
        # pairs -- the latter raises "Need named argument for struct pack".
        detail_cols = ", ".join(
            f"{c} := CAST({c} AS VARCHAR)" for c in audit.columns
        ) or "row := ''"

        try:
            checked = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

            conn.execute(
                f"""
                INSERT INTO landing.audit_failures
                SELECT ?, ?, ?, ?, {trace},
                       to_json(struct_pack({detail_cols}))
                FROM {table}
                WHERE {audit.where}
                LIMIT {MAX_FAILING_ROWS_STORED}
                """,
                [run_at, run_id, audit.name, audit.entity],
            )

            failing, files = conn.execute(
                f"SELECT COUNT(*), COUNT(DISTINCT source_file) "
                f"FROM {table} WHERE {audit.where}"
            ).fetchone()

            status = "PASSED" if failing == 0 else "FAILED"
            error = None
        except Exception as exc:  # noqa: BLE001 -- one bad audit, not the run
            context.log.error("Audit %s errored: %s", audit.name, exc)
            checked = failing = files = 0
            status, error = "ERROR", str(exc)[:2000]

        if status != "PASSED":
            worst = "ERROR" if status == "ERROR" or worst == "ERROR" else "FAILED"
        total_failing += failing

        conn.execute(
            "INSERT INTO landing.audit_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [run_at, run_id, env, audit.name, audit.entity, audit.question,
             audit.severity, status, checked, failing, files, error],
        )
        summary[audit.name] = {
            "status": status, "rows_failing": int(failing),
            "files_failing": int(files), "severity": audit.severity,
        }
        context.log.info(
            "  %-30s %-7s %6s row(s) across %s file(s)",
            audit.name, status, failing, files,
        )

    return {"summary": summary, "total_failing": total_failing, "worst": worst}


def _by_file(conn, run_id: str, audits: List[Audit]) -> str:
    """
    Failures grouped by workbook — the view a supervisor is handed.

    This is the whole reason these checks exist next to the SQLMesh audits:
    the answer to "who needs to open which file", not "did the build pass".
    """
    names = ",".join(f"'{a.name}'" for a in audits)
    rows = conn.execute(
        f"SELECT source_file, sheet_name, COUNT(*) AS failures, "
        f"       COUNT(DISTINCT audit) AS distinct_audits "
        f"FROM landing.audit_failures "
        f"WHERE run_id = ? AND audit IN ({names}) "
        f"GROUP BY 1, 2 ORDER BY failures DESC LIMIT 25",
        [run_id],
    ).fetchall()
    if not rows:
        return "No failing rows."
    return "\n".join(
        f"- `{f or 'unknown'}` / `{s or '-'}`: {n} failing row(s), {d} audit(s)"
        for f, s, n, d in rows
    )


@asset(
    group_name="quality",
    description=(
        "Runs the data quality audits and appends results to the lake, traced "
        "to the source workbook."
    ),
    compute_kind="duckdb",
    ins={"marts_validation": AssetIn()},
)
def data_quality_report(
    context: AssetExecutionContext,
    marts_validation: dict,
) -> dict:
    """
    The only asset that opens the lake for writing after ingestion.

    In the chain rather than hanging off it as a check, so nothing else holds
    the catalog at the same time. published_files depends on this.
    """
    env = _cfg.sqlmesh_env
    audits = SALES_AUDITS + KP_SD_AUDITS
    run_id = context.run_id

    conn = DuckLakeResource(read_only=False).get_connection()
    try:
        context.log.info("Running %d audit(s) against %s", len(audits), env)
        result = _run_audits(conn, audits, env, run_id, context)
        by_file = _by_file(conn, run_id, audits)
    finally:
        conn.close()

    by_entity: dict = {}
    for audit in audits:
        row = result["summary"].get(audit.name, {})
        bucket = by_entity.setdefault(audit.entity, {"failing": 0, "audits": 0})
        bucket["failing"] += row.get("rows_failing", 0)
        bucket["audits"] += 1

    context.add_output_metadata({
        "environment": env,
        "run_id": run_id,
        "audits_run": len(audits),
        "total_failing_rows": result["total_failing"],
        "by_entity": MetadataValue.json(by_entity),
        "summary": MetadataValue.json(result["summary"]),
        "by_source_file": MetadataValue.md(by_file),
    })

    return {
        "run_id": run_id,
        "environment": env,
        "worst": result["worst"],
        "total_failing": result["total_failing"],
        "summary": result["summary"],
    }


@asset_check(
    asset="data_quality_report",
    name="data_quality_checks",
    description="Reports the audit verdict. Read-only; never blocks.",
    blocking=False,
)
def data_quality_checks(context: AssetCheckExecutionContext) -> AssetCheckResult:
    """
    Reads back what data_quality_report wrote.

    READ-ONLY on purpose -- that is what lets it run in parallel with
    published_files, which is how the original single check deadlocked.
    """
    env = _cfg.sqlmesh_env
    meta = schema_for("meta", env)

    conn = DuckLakeResource(read_only=True).get_connection()
    try:
        rows = conn.execute(
            f'SELECT audit, entity, severity, status, rows_failing, files_failing '
            f'FROM "{meta}"."audit_results" WHERE is_latest '
            f"ORDER BY rows_failing DESC"
        ).fetchdf()
        worst = conn.execute(
            f'SELECT source_file, COUNT(*) AS failures FROM "{meta}"."audit_failures" '
            f"WHERE run_recency = 1 GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
        ).fetchdf()
    finally:
        conn.close()

    if rows.empty:
        return AssetCheckResult(
            passed=True,
            severity=AssetCheckSeverity.WARN,
            metadata={"note": "No audit results recorded yet."},
        )

    failing = rows[rows["status"] != "PASSED"]
    return AssetCheckResult(
        # WARN even when audits fail: these attribute problems, they do not
        # gate. SQLMesh audits stop a bad build; this says who to talk to.
        passed=failing.empty,
        severity=AssetCheckSeverity.WARN,
        metadata={
            "environment": env,
            "audits_failing": int(len(failing)),
            "total_failing_rows": int(rows["rows_failing"].sum()),
            "results": MetadataValue.md(rows.to_markdown(index=False)),
            "worst_workbooks": MetadataValue.md(
                worst.to_markdown(index=False) if not worst.empty
                else "No failing rows."
            ),
        },
    )