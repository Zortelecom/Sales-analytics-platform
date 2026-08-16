"""
reporting/pages/6_Quality_Trends.py
Pipeline health from the meta schema.

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

The meta.* views are in UNSCOPED_OBJECTS: they carry no region or subregion,
so a supervisor sees the same pipeline health an admin does.

⚠ meta.source_files and meta.extraction_coverage expose filesystem paths and
supervisor sheet names. That is fine for the current audiences; think again
before adding a role you would not show the server's directory layout to.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import reporting._bootstrap  # noqa: F401

import pandas as pd
import streamlit as st

from reporting.config import COLORS
from reporting.utils.db import query
from reporting.utils.formatters import fmt_number, fmt_pct
from reporting.components.kpi_cards import render_page_header

render_page_header("Pipeline Health", "Ingestion outcomes and data freshness", "")
st.markdown(
    f'<p style="color:{COLORS["text_secondary"]}; margin-top:0;">'
    f"Did the last run work, is every workbook still arriving, and how stale "
    f"is each source?</p>",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Batch history
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("---")
    st.markdown(
        '<p style="color:#6B7280; font-size:0.75rem; text-transform:uppercase; '
        'letter-spacing:0.05em;">Filters</p>',
        unsafe_allow_html=True,
    )
    batch_limit = st.slider("Batches to show", 5, 100, 20, key="ph_limit")

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

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.metric("Last run", str(latest["started_at"])[:16])
with c2:
    st.metric("Rows landed", fmt_number(latest["rows_landed"]))
with c3:
    st.metric("Files ingested", fmt_number(latest["files_ingested"]))
with c4:
    failed = int(latest["files_failed"] or 0)
    st.metric("Files failed", fmt_number(failed),
              delta=None if failed == 0 else "needs attention",
              delta_color="inverse")

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

freshness = query(
    "SELECT landing_table, max_business_date, business_lag_days, "
    "       last_ingested_at, arrival_lag_days, current_rows "
    "FROM freshness ORDER BY business_lag_days DESC NULLS FIRST"
)
if freshness.empty:
    st.caption("meta.freshness is empty — has `sqlmesh plan` run?")
else:
    st.dataframe(freshness, width="stretch", hide_index=True)
    stale = freshness[freshness["business_lag_days"].fillna(9_999) > 7]
    if not stale.empty:
        st.warning(
            "No transactions in the last 7 days for: "
            + ", ".join(stale["landing_table"].tolist())
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
st.dataframe(
    query(
        "SELECT landing_table, current_rows, total_rows, superseded_rows, "
        "       source_files, last_ingested_at "
        "FROM landing_inventory ORDER BY landing_table"
    ),
    width="stretch",
    hide_index=True,
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
coverage = query(
    "SELECT landing_table, source_file, sheet_name, table_name, "
    "       rows_extracted, pct_of_file "
    "FROM extraction_coverage ORDER BY rows_extracted ASC LIMIT 200"
)
st.dataframe(coverage, width="stretch", hide_index=True)

if not coverage.empty:
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
st.dataframe(
    query(
        "SELECT source_type, source_file, last_status, last_ingested_at, "
        "       days_since_ingested, days_since_modified, last_row_count, "
        "       ingest_count, last_error "
        "FROM source_files ORDER BY days_since_ingested DESC"
    ),
    width="stretch",
    hide_index=True,
)

st.markdown("---")

# ---------------------------------------------------------------------------
# Batch history table + trend
# ---------------------------------------------------------------------------
st.subheader("Batch history")

trend = batches[["started_at", "rows_landed"]].set_index("started_at").sort_index()
st.line_chart(trend, width="stretch")

st.dataframe(batches, width="stretch", hide_index=True)

total_files = int(batches["files_seen"].sum())
total_failed = int(batches["files_failed"].fillna(0).sum())
st.caption(
    f"Across the last {len(batches)} batch(es): {fmt_number(total_files)} file(s) seen, "
    f"{fmt_number(total_failed)} failed "
    f"({fmt_pct(total_failed / total_files * 100 if total_files else 0)})."
)

# ---------------------------------------------------------------------------
# Data quality — the workbook view first
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
    st.success(
        "No failing rows in the latest audit run — or the data quality checks "
        "have not run yet. They run as an asset check after marts_validation."
    )
else:
    st.subheader("Workbooks needing attention")
    st.caption(
        "Ordered by failing rows. Usually a short list: most problems "
        "concentrate in one or two workbooks, which makes this a conversation "
        "with one or two supervisors rather than a data quality initiative."
    )
    top = workbooks.iloc[0]
    total = int(workbooks["failing_rows"].sum())
    share = top["failing_rows"] / total * 100 if total else 0

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Workbooks affected", fmt_number(len(workbooks)))
    with c2:
        st.metric("Failing rows", fmt_number(total))
    with c3:
        st.metric("Worst workbook", f"{fmt_pct(share)} of all failures")

    st.dataframe(workbooks, width="stretch", hide_index=True)

    # ── Audit outcomes, newest run ────────────────────────────────────────
    st.subheader("Audit outcomes")
    st.caption(
        "is_regression flags a count that went UP since the previous run. A "
        "count that is merely high has usually been high for months; one that "
        "moved is what changed this week."
    )
    st.dataframe(
        query(
            "SELECT audit, entity, question, severity, status, rows_failing, "
            "       files_failing, change_vs_previous, is_regression, "
            "       failure_rate_pct, run_at "
            "FROM audit_results WHERE is_latest ORDER BY rows_failing DESC"
        ),
        width="stretch",
        hide_index=True,
    )

    regressions = query(
        "SELECT audit, rows_failing, previous_rows_failing, change_vs_previous "
        "FROM audit_results WHERE is_latest AND is_regression "
        "ORDER BY change_vs_previous DESC"
    )
    if not regressions.empty:
        st.warning(
            "Worse than the previous run: "
            + ", ".join(
                f"{r.audit} (+{int(r.change_vs_previous)})"
                for r in regressions.itertuples()
            )
        )

    # ── Trend ─────────────────────────────────────────────────────────────
    st.subheader("Failing rows over time")
    history = query(
        "SELECT run_at, audit, rows_failing FROM audit_results "
        "ORDER BY run_at"
    )
    if not history.empty:
        st.line_chart(
            history.pivot_table(index="run_at", columns="audit",
                                values="rows_failing", fill_value=0).sort_index(),
            width="stretch",
        )

    # ── Drill down to the rows ────────────────────────────────────────────
    st.subheader("Failing rows")
    st.caption("Capped at 500 per audit per run: a rule failing on thousands "
               "of rows is a broken rule, not thousands of problems.")
    picked = st.selectbox(
        "Workbook", ["(all)"] + workbooks["source_file"].dropna().tolist(),
        key="ph_workbook",
    )
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
    st.dataframe(rows, width="stretch", hide_index=True)

st.info(
    "These checks attribute problems; they do not gate the pipeline. SQLMesh "
    "audits stop a bad build and report inline on every `sqlmesh plan` — this "
    "page answers the other question: who needs to open which file."
)