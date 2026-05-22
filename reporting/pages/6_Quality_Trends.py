"""
reporting/pages/6_Quality_Trends.py
Query bi.quality_trend to see how each audit behaves over time.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import reporting._bootstrap  # noqa: F401

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

from reporting.config import COLORS, DB_SCHEMA
from reporting.utils.db import query
from reporting.utils.formatters import fmt_number, fmt_pct, achievement_color
from reporting.components.kpi_cards import render_page_header


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
render_page_header("Data Quality Trends", "Audit failure tracking over time", "")
st.markdown(
    f'<p style="color:{COLORS["text_secondary"]}; margin-top:0;">'
    f"Did <code>orphaned_products</code> failures increase this week?  "
    f"Filter by date range and audit below.</p>",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("---")
    st.markdown(
        '<p style="color:#6B7280; font-size:0.75rem; text-transform:uppercase; letter-spacing:0.05em;">Filters</p>',
        unsafe_allow_html=True,
    )

    # Date range
    today = datetime.now().date()
    start_date = st.date_input("From", today - timedelta(days=30), key="qt_start")
    end_date = st.date_input("To", today, key="qt_end")

    # Audit picker (populated from the table itself)
    with st.spinner("Loading audit list…"):
        audit_df = query(f"SELECT DISTINCT audit FROM {DB_SCHEMA}.quality_trend ORDER BY audit")
    all_audits = audit_df.iloc[:, 0].tolist() if not audit_df.empty else []
    selected_audits = st.multiselect(
        "Audits", all_audits, default=all_audits, key="qt_audits"
    )

# ---------------------------------------------------------------------------
# Build query
# ---------------------------------------------------------------------------
params: list = []
clauses: list[str] = []

clauses.append("run_at >= ?")
params.append(start_date.strftime("%Y-%m-%d 00:00:00"))

clauses.append("run_at <= ?")
params.append((end_date + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00"))

if selected_audits:
    placeholders = ",".join(["?"] * len(selected_audits))
    clauses.append(f"audit IN ({placeholders})")
    params.extend(selected_audits)

where = "WHERE " + " AND ".join(clauses)

sql = f"""
    SELECT run_at, audit, status, failing_rows
    FROM {DB_SCHEMA}.quality_trend
    {where}
    ORDER BY run_at DESC, audit
"""
with st.spinner("Loading quality trend data…"):
    df = query(sql, tuple(params))

if df.empty:
    st.info("No quality trend data found. Run the **data_quality_full_report** asset check first.")
    st.stop()

# ---------------------------------------------------------------------------
# KPI tiles
# ---------------------------------------------------------------------------
total_runs = int(df["run_at"].nunique())
latest_run = df["run_at"].max()
total_failed_rows = int(df[df["status"] == "FAILED"]["failing_rows"].sum())
failure_rate = (
    len(df[df["status"] == "FAILED"]) / len(df) * 100 if len(df) > 0 else 0
)

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.metric("Runs in window", fmt_number(total_runs))
with c2:
    st.metric("Latest run", str(latest_run)[:16])
with c3:
    st.metric("Failed rows", fmt_number(total_failed_rows))
with c4:
    st.metric("Failure rate", fmt_pct(failure_rate))

st.markdown("---")

# ---------------------------------------------------------------------------
# Line chart: failing rows over time by audit
# ---------------------------------------------------------------------------
st.subheader("Failure trend by audit")

pivot = (
    df.pivot_table(index="run_at", columns="audit", values="failing_rows", fill_value=0)
    .sort_index()
)

st.line_chart(pivot, use_container_width=True)

# ---------------------------------------------------------------------------
# WoW / MoM comparison helper
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Week-over-week change (last 2 runs)")

latest_two_runs = (
    df.groupby("run_at")["failing_rows"]
    .sum()
    .sort_index(ascending=False)
    .head(2)
)

if len(latest_two_runs) >= 2:
    current, previous = latest_two_runs.iloc[0], latest_two_runs.iloc[1]
    delta = current - previous
    delta_pct = (delta / previous * 100) if previous else 0

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.metric("Current run total failures", fmt_number(current))
    with col_b:
        st.metric("Previous run total failures", fmt_number(previous))
    with col_c:
        st.metric("Change", fmt_number(delta), fmt_pct(delta_pct))
else:
    st.caption("At least two runs are required to compute a week-over-week delta.")

# ---------------------------------------------------------------------------
# Detail tables
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Raw history")

status_colors = {
    "PASSED": COLORS["success"],
    "FAILED": COLORS["danger"],
    "ERROR":  COLORS["warning"],
}

def _color_status(val):
    return f"color: {status_colors.get(val, COLORS['neutral'])}"

styled = df.head(200).style.applymap(_color_status, subset=["status"])
st.dataframe(styled, use_container_width=True)

# ---------------------------------------------------------------------------
# Audit-level summary
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Audit summary")

summary_sql = f"""
    SELECT
        audit,
        COUNT(*)                                    AS total_runs,
        SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failure_count,
        SUM(failing_rows)                           AS total_failing_rows,
        AVG(CASE WHEN status = 'FAILED' THEN failing_rows END) AS avg_failing_rows,
        MAX(run_at)                                 AS last_seen
    FROM {DB_SCHEMA}.quality_trend
    {where}
    GROUP BY audit
    ORDER BY total_failing_rows DESC
"""
with st.spinner("Loading audit summary…"):
    summary_df = query(summary_sql, tuple(params))
st.dataframe(summary_df, use_container_width=True)