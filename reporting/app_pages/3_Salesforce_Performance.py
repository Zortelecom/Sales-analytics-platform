"""
reporting/pages/3_Salesforce_Performance.py
Salesforce Performance — Supervisor → Salesperson drill-down.

"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import reporting._bootstrap  # noqa: F401

import streamlit as st
from reporting.utils.filters import period_label, render_sidebar_filters
from reporting.utils.formatters import (
    fmt_currency, fmt_pct, fmt_number, achievement_color, month_name
)
from reporting.utils.queries import (
    get_salesperson_ranking,
    get_supervisor_summary,
    get_salesperson_monthly_trend,
)
from reporting.components.kpi_cards import render_kpi_row, render_section_header, render_page_header
from reporting.components.charts import (
    horizontal_bar_chart,
    revenue_vs_target_chart,
    performance_scatter,
    trend_line_chart,
)
from reporting.components.ranking_table import render_ranking_table
from reporting.config import COLORS


# ---- Filters ---------------------------------------------------------------
f = render_sidebar_filters(show_region=True, show_subregion=True, show_channel=True)
period      = f["period"]
regions     = f["regions"]
subregions  = f["subregions"]
channels    = f["channels"]
mt          = f["meeting_type"]

# region_scalar / channel_scalar are GONE. They collapsed a multiselect to its
# first element and dropped the filter entirely when two were chosen -- so
# picking Centre AND Littoral filtered by neither, which reads as a data bug on
# the page rather than a filter bug. The builders take lists now.

label = period_label(f)
render_page_header("Salesforce Performance", label, mt)


# ---- Data ------------------------------------------------------------------
with st.spinner("Loading salesforce data…"):
    sp_df  = get_salesperson_ranking(period, None, regions, None, channels,
                                     subregions)
    sup_df = get_supervisor_summary(period, None, regions, channels, subregions)

if sp_df.empty:
    st.warning("No salesforce data for the selected period.")
    st.stop()

total_rev = sp_df["revenue"].sum()
total_tgt = sp_df["target"].sum()
total_ach = round(total_rev / total_tgt * 100, 2) if total_tgt > 0 else None
n_reps    = sp_df["salesperson_id"].nunique()
above_tgt = (sp_df["achievement_pct"] >= 100).sum()


# ---- KPI Row ---------------------------------------------------------------
render_kpi_row([
    {"title": "Total Revenue", "value": fmt_currency(total_rev, short=True),
     "subtitle": f"Target: {fmt_currency(total_tgt, short=True)}", "icon": "💰"},
    {"title": "Team Achievement", "value": fmt_pct(total_ach),
     "accent_color": achievement_color(total_ach), "icon": "🎯"},
    {"title": "Sales Reps", "value": fmt_number(n_reps), "icon": "👤"},
    {"title": "Above Target", "value": f"{above_tgt}/{n_reps}",
     "subtitle": f"{round(above_tgt/n_reps*100) if n_reps else 0}% of team",
     "accent_color": COLORS["success"], "icon": "✅"},
    {"title": "Supervisors", "value": fmt_number(len(sup_df)),
     "accent_color": COLORS["chart"][2], "icon": "🏅"},
])

st.markdown('<div class="spacer-md"></div>', unsafe_allow_html=True)


# ---- Tabs ------------------------------------------------------------------
tab1, tab2, tab3, tab4 = st.tabs([
    "👥 Team Ranking",
    "🏅 Supervisor View",
    "🔍 Individual Drill-down",
    "📡 Scatter Analysis",
])


# ====== TAB 1: Full Team Ranking ============================================
with tab1:
    col_bar, col_tbl = st.columns([2, 3])

    with col_bar:
        top20 = sp_df.head(20).copy()
        horizontal_bar_chart(
            top20.sort_values("revenue"),
            y_col="salesperson_name",
            x_col="revenue",
            color_col="achievement_pct",
            title="Top Salesperson Revenue",
            height=max(300, len(top20) * 30),
            max_rows=20,
        )

    with col_tbl:
        render_ranking_table(
            sp_df,
            name_col="salesperson_name",
            title="Salesperson Ranking",
            extra_cols=["region", "supervisor_name", "active_clients"],
            max_rows=30,
        )


# ====== TAB 2: Supervisor View ==============================================
with tab2:
    if sup_df.empty:
        st.info("No supervisor data available.")
    else:
        col_sup_bar, col_sup_tbl = st.columns([2, 3])

        with col_sup_bar:
            horizontal_bar_chart(
                sup_df.sort_values("revenue"),
                y_col="supervisor_name",
                x_col="revenue",
                color_col="achievement_pct",
                title="Revenue by Supervisor",
                height=max(280, len(sup_df) * 38),
            )

        with col_sup_tbl:
            render_ranking_table(
                sup_df,
                name_col="supervisor_name",
                title="Supervisor Summary",
                extra_cols=["region", "team_size"],
            )

        st.markdown('<div class="spacer-sm"></div>', unsafe_allow_html=True)
        render_section_header("Salesperson → Supervisor Attribution")

        selected_sup = st.selectbox(
            "Select Supervisor",
            ["All"] + sup_df["supervisor_name"].tolist(),
            key="sup_select",
        )
        sup_team_df = sp_df if selected_sup == "All" else sp_df[sp_df["supervisor_name"] == selected_sup]
        render_ranking_table(
            sup_team_df,
            name_col="salesperson_name",
            title=f"Team: {selected_sup}",
            extra_cols=["region", "active_clients"],
            max_rows=20,
        )


# ====== TAB 3: Individual Drill-down ========================================
with tab3:
    sp_names = sp_df[["salesperson_id", "salesperson_name"]].drop_duplicates()
    sp_options = sp_names.apply(lambda r: f"{r.salesperson_name} ({r.salesperson_id})", axis=1).tolist()
    selected_sp_label = st.selectbox("Select Salesperson", sp_options, key="sp_select")
    sp_id = sp_names.iloc[sp_options.index(selected_sp_label)]["salesperson_id"]

    sp_row = sp_df[sp_df["salesperson_id"] == sp_id].iloc[0]

    st.markdown('<div class="spacer-sm"></div>', unsafe_allow_html=True)
    render_kpi_row([
        {"title": "Revenue", "value": fmt_currency(sp_row["revenue"], short=True),
         "subtitle": f"Target: {fmt_currency(sp_row['target'], short=True)}", "icon": "💰"},
        {"title": "Achievement", "value": fmt_pct(sp_row.get("achievement_pct")),
         "accent_color": achievement_color(sp_row.get("achievement_pct")), "icon": "🎯"},
        {"title": "Units Sold", "value": fmt_number(sp_row.get("units_sold")), "icon": "📦"},
        {"title": "Active Clients", "value": fmt_number(sp_row.get("active_clients")), "icon": "🏪"},
        {"title": "Supervisor", "value": str(sp_row.get("supervisor_name","—")),
         "accent_color": COLORS["chart"][2], "icon": "🏅"},
    ])

    st.markdown('<div class="spacer-sm"></div>', unsafe_allow_html=True)
    with st.spinner("Loading monthly trend…"):
        sp_trend = get_salesperson_monthly_trend(period.whole(), sp_id)
    revenue_vs_target_chart(
        sp_trend,
        x_col="month",
        x_label_fn=month_name,
        title=f"{sp_row['salesperson_name']} — Monthly Performance {period.label}",
        height=280,
    )


# ====== TAB 4: Scatter Analysis =============================================
with tab4:
    render_section_header(
        "Revenue vs Target — Bubble Chart",
        "Bubble size = active clients. Diagonal = 100% achievement."
    )
    performance_scatter(
        sp_df,
        x_col="target",
        y_col="revenue",
        size_col="active_clients",
        label_col="salesperson_name",
        color_col="achievement_pct",
        title=f"Salesperson Performance Matrix — {label}",
        height=420,
    )