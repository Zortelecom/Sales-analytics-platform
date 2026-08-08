"""KP sell-in versus SD sell-out reconciliation.
"""
from __future__ import annotations

import reporting._bootstrap  # noqa: F401
import streamlit as st

from reporting.components.kpi_cards import render_page_header
from reporting.utils.db import query
from reporting.utils.formatters import fmt_number
from reporting.utils.views import KP_SD_BASE, SELLIN_SELLOUT_KPI

render_page_header("Sell-In / Sell-Out", "KP sell-in reconciliation by SD and product", "")

# 1. Year Filter Setup
years = query(f"SELECT DISTINCT year FROM {SELLIN_SELLOUT_KPI} ORDER BY year DESC")
if years.empty:
    st.info("No sell-in data is available yet. Run the KP-SD ingestion pipeline first.")
    st.stop()

year = st.sidebar.selectbox("Year", years.iloc[:, 0].tolist(), key="sellin_year")

# 2. Month Filter Setup
months = query(f"SELECT DISTINCT month FROM {SELLIN_SELLOUT_KPI} WHERE year = ? ORDER BY month", (year,))
month = st.sidebar.selectbox("Month", ["All"] + months.iloc[:, 0].tolist(), key="sellin_month")

where, params = "WHERE year = ?", (year,)
if month != "All":
    where, params = where + " AND month = ?", (year, month)

# 3. Fetch Core KPI Data
df = query(f"SELECT * FROM {SELLIN_SELLOUT_KPI} {where}", params)
if df.empty:
    st.info("No records match these filters.")
    st.stop()

# 4. Metrics Cards
sell_in, sell_out = float(df.sell_in_revenue.sum()), float(df.sell_out_revenue.sum())
ratio = sell_out / sell_in * 100 if sell_in else None

c1, c2, c3 = st.columns(3)
c1.metric("Sell-in", fmt_number(sell_in))
c2.metric("Sell-out", fmt_number(sell_out))
c3.metric("Sell-through", f"{ratio:.1f}%" if ratio is not None else "—")

# 5. Reconciliation Table
st.subheader("Reconciliation")
st.dataframe(df.sort_values(["sell_in_revenue", "sell_out_revenue"], ascending=False), use_container_width=True, hide_index=True)

# 6. Flag Mismatch Section (L1 Fix Integrated)
mismatch_where = "WHERE sale_year = ? AND destocked_flag_mismatch"
mismatch_params: tuple = (year,)
if month != "All":
    mismatch_where = "WHERE sale_year = ? AND sale_month = ? AND destocked_flag_mismatch"
    mismatch_params = (year, month)

mismatches = query(
    f"SELECT clientsd_id, client_name, sale_date, sku, source_asserted_destocked "
    f"FROM {KP_SD_BASE} {mismatch_where}",
    mismatch_params,
)

if not mismatches.empty:
    st.warning(f"{len(mismatches):,} sell-in row(s) disagree with the SD destocked flag.")
    with st.expander("Review flag mismatches"):
        st.dataframe(mismatches, use_container_width=True, hide_index=True)