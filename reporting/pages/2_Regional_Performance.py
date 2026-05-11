"""
reporting/pages/2_Regional_Performance.py
Regional Performance — Region → Subregion drill-down.

Fix applied:
  - sys.path.insert restored before bootstrap import (both are required: insert
    makes 'reporting' importable; _bootstrap provides idempotency for other entry points)
"""
import sys
from pathlib import Path
# Add project root to sys.path so the 'reporting' package is importable,
# then import _bootstrap which keeps it idempotent for other entry points.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import reporting._bootstrap  # noqa: F401

import streamlit as st
import pandas as pd
from reporting.utils.filters import render_sidebar_filters, MONTH_NAMES
from reporting.utils.formatters import (
    fmt_currency, fmt_pct, fmt_number, achievement_color, achievement_emoji, month_name
)
from reporting.utils.queries import (
    get_regional_summary,
    get_region_monthly_trend,
    get_region_category_breakdown,
)
from reporting.components.kpi_cards import render_kpi_row, render_section_header, kpi_card
from reporting.components.charts import (
    revenue_vs_target_chart,
    horizontal_bar_chart,
    trend_line_chart,
    donut_chart,
)
from reporting.components.ranking_table import render_ranking_table
from reporting.config import COLORS


# ---- Filters ---------------------------------------------------------------
f = render_sidebar_filters(show_region=True, show_channel=True)
year     = f["year"]
month    = f["month"]
channels = f["channels"]
mt       = f["meeting_type"]

period_label = (
    f"{MONTH_NAMES.get(month,'')} {year}" if month else
    f"Q{f['quarter']} {year}" if f["quarter"] else f"Full Year {year}"
)

# ---- Page Header -----------------------------------------------------------
st.markdown(
    f"""
    <div style="display:flex; align-items:center; justify-content:space-between;
                margin-bottom:1.2rem; border-bottom:1px solid {COLORS['border']};
                padding-bottom:0.8rem;">
        <div>
            <h1 style="margin:0; font-size:1.6rem; font-weight:700;">Regional Performance</h1>
            <p style="margin:0; color:{COLORS['text_secondary']}; font-size:0.85rem;">
                {period_label} &nbsp;·&nbsp; {mt} Report
            </p>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ---- Data ------------------------------------------------------------------
reg_df = get_regional_summary(year, month, channels)

if reg_df.empty:
    st.warning("No regional data for the selected period.")
    st.stop()

# Aggregate to region level
region_totals = (
    reg_df.groupby("region")
    .agg(revenue=("revenue","sum"), target=("target","sum"),
         units_sold=("units_sold","sum"), weight_kg=("weight_kg","sum"),
         active_clients=("active_clients","sum"))
    .reset_index()
)
region_totals["achievement_pct"] = region_totals.apply(
    lambda r: round(r.revenue/r.target*100, 2) if r.target > 0 else None, axis=1
)
region_totals = region_totals.sort_values("revenue", ascending=False)


# ---- Summary KPIs ----------------------------------------------------------
total_rev = region_totals["revenue"].sum()
total_tgt = region_totals["target"].sum()
total_ach = round(total_rev / total_tgt * 100, 2) if total_tgt > 0 else None
best_row  = region_totals.iloc[0] if not region_totals.empty else {}
worst_row = region_totals.iloc[-1] if len(region_totals) > 1 else {}

render_kpi_row([
    {"title": "Total Revenue", "value": fmt_currency(total_rev, short=True),
     "subtitle": f"Target: {fmt_currency(total_tgt, short=True)}", "icon": "💰"},
    {"title": "Overall Achievement", "value": fmt_pct(total_ach),
     "accent_color": achievement_color(total_ach), "icon": "🎯"},
    {"title": "Best Region", "value": str(best_row.get("region", "—")),
     "subtitle": f"{fmt_pct(best_row.get('achievement_pct'))} achievement",
     "accent_color": COLORS["success"], "icon": "🏆"},
    {"title": "Needs Attention", "value": str(worst_row.get("region", "—")),
     "subtitle": f"{fmt_pct(worst_row.get('achievement_pct'))} achievement",
     "accent_color": COLORS["danger"], "icon": "⚠️"},
    {"title": "Active Regions", "value": fmt_number(len(region_totals)),
     "accent_color": COLORS["chart"][2], "icon": "🗺️"},
])

st.markdown('<div class="spacer-md"></div>', unsafe_allow_html=True)


# ---- Tabs: Overview | Drill-down | Category --------------------------------
tab1, tab2, tab3 = st.tabs(["📊 Overview", "🔍 Region Drill-down", "📦 By Category"])


# ========= TAB 1: Overview ==================================================
with tab1:
    col_bar, col_tbl = st.columns([2, 3])

    with col_bar:
        horizontal_bar_chart(
            region_totals.sort_values("revenue"),
            y_col="region",
            x_col="revenue",
            color_col="achievement_pct",
            title="Revenue by Region",
            height=max(280, len(region_totals) * 38),
        )

    with col_tbl:
        render_ranking_table(
            region_totals,
            name_col="region",
            title="Regional Ranking",
            extra_cols=["units_sold", "active_clients"],
        )

    st.markdown('<div class="spacer-sm"></div>', unsafe_allow_html=True)

    render_section_header("Region × Subregion Matrix")
    pivot_df = reg_df.pivot_table(
        index="region", columns="subregion",
        values="achievement_pct", aggfunc="mean"
    ).round(1)

    if not pivot_df.empty:
        def color_cell(v):
            c = achievement_color(v if pd.notna(v) else None)
            return f"color:{c}; font-weight:600;" if pd.notna(v) else "color:#374151;"

        rows_html = ""
        for region, row in pivot_df.iterrows():
            cells = "".join(
                f'<td style="{color_cell(v)}; text-align:center; padding:0.3rem 0.5rem;">'
                f'{fmt_pct(v) if pd.notna(v) else "—"}</td>'
                for v in row
            )
            rows_html += f"""
            <tr>
                <td style="color:{COLORS['text_primary']}; padding:0.3rem 0.5rem;
                           font-weight:500;">{region}</td>
                {cells}
            </tr>
            """
        headers = "".join(
            f'<th style="padding:0.3rem 0.5rem; text-align:center;">{col}</th>'
            for col in pivot_df.columns
        )
        st.markdown(f"""
        <div style="overflow-x:auto;">
        <table style="width:100%; border-collapse:collapse; font-size:0.83rem;">
            <thead>
            <tr style="background:{COLORS['bg_card_alt']}; color:{COLORS['text_secondary']};
                       font-size:0.72rem; text-transform:uppercase; letter-spacing:0.05em;">
                <th style="text-align:left; padding:0.4rem 0.5rem;">Region</th>
                {headers}
            </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
        </div>
        """, unsafe_allow_html=True)


# ========= TAB 2: Drill-down ================================================
with tab2:
    regions_list = region_totals["region"].tolist()
    selected_region = st.selectbox("Select Region", regions_list, key="drilldown_region")

    if selected_region:
        sub_df = reg_df[reg_df["region"] == selected_region].copy()

        r_rev = sub_df["revenue"].sum()
        r_tgt = sub_df["target"].sum()
        r_ach = round(r_rev / r_tgt * 100, 2) if r_tgt > 0 else None

        st.markdown('<div class="spacer-sm"></div>', unsafe_allow_html=True)
        render_kpi_row([
            {"title": f"{selected_region} Revenue", "value": fmt_currency(r_rev, short=True),
             "subtitle": f"Target: {fmt_currency(r_tgt, short=True)}", "icon": "💰"},
            {"title": "Achievement", "value": fmt_pct(r_ach),
             "accent_color": achievement_color(r_ach), "icon": "🎯"},
            {"title": "Subregions", "value": fmt_number(len(sub_df)),
             "accent_color": COLORS["chart"][2], "icon": "📍"},
            {"title": "Clients", "value": fmt_number(sub_df["active_clients"].sum()),
             "accent_color": COLORS["chart"][3], "icon": "🏪"},
        ])

        st.markdown('<div class="spacer-sm"></div>', unsafe_allow_html=True)

        col_trend, col_sub = st.columns([3, 2])
        with col_trend:
            trend_data = get_region_monthly_trend(year, selected_region)
            revenue_vs_target_chart(
                trend_data,
                x_col="month",
                x_label_fn=month_name,
                title=f"{selected_region} — Monthly Trend",
                height=280,
            )

        with col_sub:
            render_ranking_table(
                sub_df.sort_values("revenue", ascending=False),
                name_col="subregion",
                title="Subregion Breakdown",
                rank_col=True,
            )


# ========= TAB 3: Category ==================================================
with tab3:
    cat_reg_df = get_region_category_breakdown(year, month)

    if not cat_reg_df.empty:
        regions_for_cat = ["All Regions"] + sorted(cat_reg_df["region"].dropna().unique().tolist())
        sel_region_cat = st.selectbox("Filter by Region", regions_for_cat, key="cat_region")

        if sel_region_cat != "All Regions":
            display_cat_df = cat_reg_df[cat_reg_df["region"] == sel_region_cat]
        else:
            display_cat_df = (
                cat_reg_df.groupby("product_category")
                .agg(revenue=("revenue","sum"), target=("target","sum"),
                     units_sold=("units_sold","sum"))
                .reset_index()
            )
            display_cat_df["achievement_pct"] = display_cat_df.apply(
                lambda r: round(r.revenue/r.target*100,2) if r.target > 0 else None, axis=1
            )

        col_donut, col_cat_rank = st.columns([2, 3])
        with col_donut:
            donut_chart(display_cat_df, "product_category", "revenue",
                       title="Revenue Share by Category", height=300)
        with col_cat_rank:
            render_ranking_table(
                display_cat_df.sort_values("revenue", ascending=False),
                name_col="product_category",
                title="Category Performance",
                extra_cols=["units_sold"],
            )
