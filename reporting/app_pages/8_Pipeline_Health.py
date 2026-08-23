"""
reporting/app_pages/8_Pipeline_Health.py
Pipeline health and data quality, from the meta schema.

Registered in app.py as page key "quality", title "Data Quality", position 8.
The numeric prefix is decorative: `reporting/pages/` was renamed to
`app_pages/` to kill Streamlit's legacy auto-discovery, so nav order comes
from _ALL_PAGES in app.py, not from the filename.

(2026-08) REBUILT. This page read bi.quality_trend, a table the Dagster
data_quality_full_report asset wrote into serving.db. serving.db is gone and
that table exists nowhere in the lake, so the page could only ever show its
"run the asset check first" message.

What replaces it, and what does not:

  ✔ ingestion outcomes, per batch      meta.ingestion_batches
  ✔ data freshness per source          meta.freshness
  ✔ rows landed and superseded         meta.landing_inventory
  ✔ rows per supervisor sheet          meta.extraction_coverage
  ✔ files that stopped arriving        meta.source_files

  ✔ audit outcomes and trend           meta.audit_results
  ✔ which WORKBOOK is problematic      meta.workbook_health
  ✔ the failing rows themselves        meta.audit_failures

The workbook view is the reason this page exists. A SQLMesh audit reports
"12 rows failed" on the console; converting that into "Banok Serge's tab in
ExSD-Sales-Est.xlsx has 12 unmatched SKUs" required a hand-written query,
which is not something a supervisor will run. That translation is now done for
them.

ACCESS
   Gated by `pages` in secrets.toml, not by RLS. Neither rbm nor supervisor
   lists "quality", and app.py builds st.navigation() from the filtered list,
   so an excluded page is not merely hidden -- it is never constructed and no
   URL reaches it. Only admin and analyst get here, and both are unscoped, so
   apply_rls() returns early and none of the meta objects is ever tested
   against the map.

   Which makes `workbook_health`, `audit_results` and `audit_failures` being
   absent from UNSCOPED_OBJECTS latent, not broken. Add them anyway:
   `python -m reporting.auth.introspect` reports them as unfinished map
   entries, and adding "quality" to one supervisor's `pages` would turn this
   whole section into an RLS refusal for a reason nobody would look for here.

   They are unscopable by nature, not by oversight: an audit failure is
   attributed to a workbook and a sheet, not to a region. Scoping them would
   mean joining audit_failures -> landing -> a business dimension that a
   failing row, by definition, may not have parsed into.

⚠ meta.source_files and meta.extraction_coverage expose filesystem paths and
supervisor sheet names. That is fine for the current audiences; think again
before adding a role you would not show the server's directory layout to.
"""
from __future__ import annotations

# app.py fixes sys.path inline before st.navigation() constructs any page, so
# the hand-rolled `sys.path.insert(... parent.parent.parent)` that used to sit
# here is dead weight -- and it pointed one level too shallow now that pages
# live in app_pages/. _bootstrap alone is correct and self-locating.
import reporting._bootstrap  # noqa: F401

import pandas as pd
import streamlit as st

from reporting.utils.db import query
from reporting.utils.formatters import fmt_number, fmt_pct
from reporting.components.kpi_cards import render_page_header


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _int_or_zero(value) -> int:
    """Coerce a possibly-NULL count to int.

    `int(value or 0)` was wrong: a NULL arrives from DuckDB as float NaN,
    NaN is truthy, so `NaN or 0` yields NaN and `int(NaN)` raises ValueError
    -- taking the whole page down on the first batch that recorded no
    files_failed. pd.isna() is the check that actually holds.
    """
    return 0 if value is None or pd.isna(value) else int(value)


def _table(sql: str, params: tuple = (), *, empty: str) -> pd.DataFrame:
    """Run a meta query and render it, or explain the blank.

    Every section did `st.dataframe(query(...))` directly, which renders an
    empty grid with no explanation when the query fails or returns nothing --
    exactly what the extraction_coverage binder error produced. An empty
    result and a broken query look identical to the reader either way, so
    the caption has to cover both.
    """
    df = query(sql, params)
    if df.empty:
        st.caption(empty)
    else:
        st.dataframe(df, width="stretch", hide_index=True)
    return df


render_page_header("Pipeline Health", "Ingestion outcomes and data freshness", "")
st.caption(
    "Did the last run work, is every workbook still arriving, and how stale "
    "is each source?"
)

with st.sidebar:
    st.markdown("---")
    st.caption("FILTERS")
    batch_limit = st.slider("Batches to show", 5, 100, 20, key="ph_limit")


# ---------------------------------------------------------------------------
# Batch history — the gate for the rest of the page
# ---------------------------------------------------------------------------

batches = query(
    "SELECT batch_id, started_at, finished_at, duration_seconds, "
    "       files_seen, files_ingested, files_skipped, files_failed, "
    "       tables_written, rows_landed, source_types "
    "FROM ingestion_batches ORDER BY started_at DESC LIMIT ?",
    (batch_limit,),
)

if batches.empty:
    st.info(
        "No ingestion batches recorded yet. Run `python -m ingestion.main --all`, "
        "then `sqlmesh plan dev` so the meta views are built."
    )
    st.stop()

latest = batches.iloc[0]
failed = _int_or_zero(latest["files_failed"])

c1, c2, c3, c4 = st.columns(4)
c1.metric("Last run", str(latest["started_at"])[:16])
c2.metric("Rows landed", fmt_number(latest["rows_landed"]))
c3.metric("Files ingested", fmt_number(latest["files_ingested"]))
c4.metric(
    "Files failed", fmt_number(failed),
    delta=None if failed == 0 else "needs attention",
    delta_color="inverse",
)

if failed:
    st.error(
        f"{failed} file(s) failed in the latest batch. Check "
        f"`data/logs/failures_{latest['batch_id']}.txt` and `data/dead_letter/`."
    )

st.markdown("---")


# ---------------------------------------------------------------------------
# Freshness — the number that matters most and is easiest to miss
# ---------------------------------------------------------------------------

st.subheader("Freshness by source")
st.caption(
    "Business lag is how old the newest TRANSACTION is. Arrival lag is how "
    "long since the file was ingested. A workbook can arrive on time and "
    "contain nothing new — only the first number catches that."
)

freshness = _table(
    "SELECT landing_table, max_business_date, business_lag_days, "
    "       last_ingested_at, arrival_lag_days, current_rows "
    "FROM freshness ORDER BY business_lag_days DESC NULLS FIRST",
    empty="meta.freshness returned nothing — has `sqlmesh plan` run?",
)

if not freshness.empty:
    # NULLS FIRST puts a source with no business date at the top, where it
    # belongs: no transactions at all is worse than stale ones, not better.
    stale = freshness[freshness["business_lag_days"].fillna(9_999) > 7]
    if not stale.empty:
        st.warning(
            "No transactions in the last 7 days for: "
            + ", ".join(stale["landing_table"].astype(str))
            + ". Expected over a holiday or a supervisor's leave; worth a call "
              "otherwise."
        )

st.markdown("---")


# ---------------------------------------------------------------------------
# Landing inventory
# ---------------------------------------------------------------------------

st.subheader("Landing inventory")
st.caption(
    "superseded_rows counts rows from an EARLIER version of a workbook that a "
    "later submission replaced. They stay queryable — that is what makes a "
    "correction measurable rather than invisible."
)
_table(
    "SELECT landing_table, current_rows, total_rows, superseded_rows, "
    "       source_files, last_ingested_at "
    "FROM landing_inventory ORDER BY landing_table",
    empty="meta.landing_inventory returned nothing.",
)

st.markdown("---")


# ---------------------------------------------------------------------------
# Extraction coverage — catches a sheet that quietly emptied
# ---------------------------------------------------------------------------

st.subheader("Rows per source sheet")
st.caption(
    "A tab a supervisor cleared, renamed or pasted over produces a structurally "
    "valid table with almost no rows, and no error anywhere. Sorted ascending "
    "so those surface first."
)

# The full projection currently raises a DuckDB INTERNAL binder error
# ("Failed to bind column reference \"\"") somewhere inside the view. The
# reduced projection drops pct_of_file, the one computed column, which
# isolates whether that expression is the cause AND keeps the section usable
# meanwhile -- the counts are what a supervisor reads; the percentage is a
# convenience.
_COVERAGE_FULL = (
    "SELECT landing_table, source_file, sheet_name, table_name, "
    "       rows_extracted, pct_of_file "
    "FROM extraction_coverage ORDER BY rows_extracted ASC LIMIT 200"
)
_COVERAGE_REDUCED = (
    "SELECT landing_table, source_file, sheet_name, table_name, rows_extracted "
    "FROM extraction_coverage ORDER BY rows_extracted ASC LIMIT 200"
)

coverage = query(_COVERAGE_FULL, silent=True)
degraded = False
if coverage.empty:
    coverage = query(_COVERAGE_REDUCED, silent=True)
    degraded = not coverage.empty

if coverage.empty:
    st.warning(
        "meta.extraction_coverage could not be read. If the log shows "
        "`INTERNAL Error: Failed to bind column reference \"\"`, a column with "
        "a blank name reached the view — check with:\n\n"
        "```sql\nSELECT table_name, column_name FROM duckdb_columns()\n"
        "WHERE column_name IS NULL OR TRIM(column_name) = '';\n```\n\n"
        "Fix it in the extractor (synthesise `unnamed_<position>` for blank "
        "headers), not in the view — a blank header is a workbook defect that "
        "meta.column_inventory exists to surface."
    )
else:
    if degraded:
        st.info(
            "Showing row counts without `pct_of_file`: the full projection "
            "failed to bind, which points at that computed column in the view "
            "definition."
        )
    st.dataframe(coverage, width="stretch", hide_index=True)

    thin = coverage[coverage["rows_extracted"] < 5]
    if not thin.empty:
        st.warning(
            f"{len(thin)} sheet(s) produced fewer than 5 rows. A header-only "
            f"template is harmless; a sheet that used to hold hundreds is not."
        )

st.markdown("---")


# ---------------------------------------------------------------------------
# Source files
# ---------------------------------------------------------------------------

st.subheader("Source workbooks")
st.caption(
    "ingest_count above 1 means a workbook was resubmitted — the earlier "
    "version is still in landing, superseded."
)
_table(
    "SELECT source_type, source_file, last_status, last_ingested_at, "
    "       days_since_ingested, days_since_modified, last_row_count, "
    "       ingest_count, last_error "
    "FROM source_files ORDER BY days_since_ingested DESC",
    empty="meta.source_files returned nothing.",
)

st.markdown("---")


# ---------------------------------------------------------------------------
# Batch history
# ---------------------------------------------------------------------------

st.subheader("Batch history")

st.line_chart(
    batches[["started_at", "rows_landed"]].set_index("started_at").sort_index(),
    width="stretch",
)
st.dataframe(batches, width="stretch", hide_index=True)

total_files = _int_or_zero(batches["files_seen"].sum())
total_failed = _int_or_zero(batches["files_failed"].sum())
st.caption(
    f"Across the last {len(batches)} batch(es): {fmt_number(total_files)} file(s) "
    f"seen, {fmt_number(total_failed)} failed "
    f"({fmt_pct(total_failed / total_files * 100 if total_files else 0)})."
)


# ---------------------------------------------------------------------------
# Data quality
#
# Audit outcomes render UNCONDITIONALLY. Previously the whole section sat
# inside `else:` on `workbooks.empty`, so a clean run hid the audit table --
# including any audit whose count had just dropped to zero, which is the one
# result worth seeing after a fix. "Nothing failing" and "nothing checked"
# also looked identical, and only one of them is good news.
# ---------------------------------------------------------------------------

st.markdown("---")
st.header("Data quality")

workbooks = query(
    "SELECT source_file, source_type, failing_rows, error_severity_rows, "
    "       distinct_audits, sheets_affected, sheets, audits, "
    "       days_since_ingested, checked_at "
    "FROM workbook_health "
    "WHERE failing_rows > 0 ORDER BY failing_rows DESC"
)

if workbooks.empty:
    st.success("No workbook has failing rows in the latest audit run.")
else:
    st.subheader("Workbooks needing attention")
    st.caption(
        "Ordered by failing rows. Usually a short list: most problems "
        "concentrate in one or two workbooks, which makes this a conversation "
        "with one or two supervisors rather than a data quality initiative."
    )

    total = _int_or_zero(workbooks["failing_rows"].sum())
    top = workbooks.iloc[0]
    share = _int_or_zero(top["failing_rows"]) / total * 100 if total else 0

    c1, c2, c3 = st.columns(3)
    c1.metric("Workbooks affected", fmt_number(len(workbooks)))
    c2.metric("Failing rows", fmt_number(total))
    c3.metric("Worst workbook", f"{fmt_pct(share)} of all failures")

    st.dataframe(workbooks, width="stretch", hide_index=True)

# ── Audit outcomes, newest run ──────────────────────────────────────────────
st.subheader("Audit outcomes")
st.caption(
    "is_regression flags a count that went UP since the previous run. A count "
    "that is merely high has usually been high for months; one that moved is "
    "what changed this week."
)
outcomes = _table(
    "SELECT audit, entity, question, severity, status, rows_failing, "
    "       files_failing, change_vs_previous, is_regression, "
    "       failure_rate_pct, run_at "
    "FROM audit_results WHERE is_latest ORDER BY rows_failing DESC",
    empty=(
        "No audit results recorded. The data quality checks run as an asset "
        "check after marts_validation — materialise it, or run "
        "`dagster asset materialize --select marts_validation`."
    ),
)

if not outcomes.empty:
    regressions = outcomes[outcomes["is_regression"].fillna(False).astype(bool)]
    if not regressions.empty:
        # Filtered in pandas rather than re-queried: same run, same rows,
        # one round trip instead of two, and no risk of the two queries
        # landing either side of a fresh audit write.
        st.warning(
            "Worse than the previous run: "
            + ", ".join(
                f"{r.audit} (+{_int_or_zero(r.change_vs_previous)})"
                for r in regressions.sort_values(
                    "change_vs_previous", ascending=False
                ).itertuples()
            )
        )

    # ── Trend ───────────────────────────────────────────────────────────────
    st.subheader("Failing rows over time")
    history = query(
        "SELECT run_at, audit, rows_failing FROM audit_results ORDER BY run_at"
    )
    if history.empty or history["run_at"].nunique() < 2:
        st.caption(
            "A trend needs at least two runs; there is only one so far."
        )
    else:
        st.line_chart(
            history.pivot_table(
                index="run_at", columns="audit",
                values="rows_failing", fill_value=0,
            ).sort_index(),
            width="stretch",
        )

    # ── Drill down to the rows ──────────────────────────────────────────────
    st.subheader("Failing rows")
    st.caption(
        "Capped at 500 per audit per run: a rule failing on thousands of rows "
        "is a broken rule, not thousands of problems."
    )

    # Options come from workbook_health, so the picker is empty exactly when
    # there is nothing to drill into.
    options = ["(all)"] + workbooks["source_file"].dropna().astype(str).tolist()
    picked = st.selectbox("Workbook", options, key="ph_workbook")

    if picked == "(all)":
        rows = query(
            "SELECT audit, source_file, sheet_name, source_row_num, detail "
            "FROM audit_failures WHERE run_recency = 1 "
            "ORDER BY source_file, sheet_name, source_row_num LIMIT 500"
        )
    else:
        rows = query(
            "SELECT audit, sheet_name, source_row_num, detail "
            "FROM audit_failures WHERE run_recency = 1 AND source_file = ? "
            "ORDER BY sheet_name, source_row_num LIMIT 500",
            (picked,),
        )

    if rows.empty:
        st.caption("No failing rows recorded for the latest run.")
    else:
        st.dataframe(rows, width="stretch", hide_index=True)

st.info(
    "These checks attribute problems; they do not gate the pipeline. SQLMesh "
    "audits stop a bad build and report inline on every `sqlmesh plan` — this "
    "page answers the other question: who needs to open which file."
)