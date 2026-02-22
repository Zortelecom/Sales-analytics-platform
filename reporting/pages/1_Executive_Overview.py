"""
reporting/pages/1_Executive_Overview.py
Executive Overview — top-level KPIs, achievement gauge, YoY trend.
Suitable for: Weekly briefing banner, Monthly/Quarterly/Annual reviews.
"""
import streamlit as st
from reporting.utils.filters import render_sidebar_filters, MONTH_NAMES
from reporting.utils.formatters import fmt_currency, fmt_pct, fmt_number, fmt_delta
from reporting.utils.queries import (
    get_executive_kpis,
    get_monthly_trend,
    get_ytd_vs_prior_year,
    get_top_regions,
    get_category_performance,
)
from reporting.components.kpi_cards import render_kpi_row, render_section_header, kpi_card
from reporting.components.charts import (
    revenue_vs_target_chart,
    achievement_gauge,
    trend_line_chart,
    donut_chart,
    horizontal_bar_chart,
)
from reporting.components.ranking_table import render_ranking_table
from reporting.utils.formatters import month_name, achievement_color
from reporting.config import COLORS


# ---- Filters ---------------------------------------------------------------
f = render_sidebar_filters(show_month=True, show_region=True, show_channel=True)
year      = f["year"]
month     = f["month"]
regions   = f["regions"]
channels  = f["channels"]
mt        = f["meeting_type"]


# ---- Page Header -----------------------------------------------------------
period_label = (
    f"Week of {MONTH_NAMES.get(month,'')} {year}" if mt == "Weekly" and month else
    f"{MONTH_NAMES.get(month,'')} {year}" if month else
    f"Q{f['quarter']} {year}" if f["quarter"] else
    f"Full Year {year}"
)

st.markdown(
    f"""
    <div style="display:flex; align-items:center; justify-content:space-between;
                margin-bottom:1.2rem; border-bottom:1px solid {COLORS['border']};
                padding-bottom:0.8rem;">
        <div>
            <h1 style="margin:0; font-size:1.6rem; font-weight:700;
                       color:{COLORS['text_primary']};">Executive Overview</h1>
            <p style="margin:0; color:{COLORS['text_secondary']}; font-size:0.85rem;">
                {period_label} &nbsp;·&nbsp; {mt} Report
            </p>
        </div>
        <div style="background:{COLORS['bg_card']}; border:1px solid {COLORS['border']};
                    border-radius:6px; padding:0.4rem 0.9rem; font-size:0.78rem;
                    color:{COLORS['text_secondary']};">
            📅 {mt.upper()} MEETING
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ---- KPI Data --------------------------------------------------------------
kpi_df = get_executive_kpis(year, month)
kpi    = kpi_df.iloc[0] if not kpi_df.empty else {}

# Prior period for delta
prior_month = (month - 1) if (month and month > 1) else None
kpi_py_df  = get_executive_kpis(year - 1, month)
kpi_py     = kpi_py_df.iloc[0] if not kpi_py_df.empty else {}


def _delta(key, fmt_fn=fmt_currency):
    curr = float(kpi.get(key, 0) or 0)
    prev = float(kpi_py.get(key, 0) or 0)
    if prev > 0:
        pct = (curr - prev) / prev * 100
        sign = "▲" if pct >= 0 else "▼"
        return f"{sign} {abs(pct):.1f}% vs PY", pct >= 0
    return "", True


# ---- Top KPI Row -----------------------------------------------------------
d_rev, d_rev_pos = _delta("total_revenue")
d_units, d_units_pos = _delta("total_units", fmt_number)
d_clients, d_clients_pos = _delta("total_clients", fmt_number)

render_kpi_row([
    {
        "title": "Total Revenue",
        "value": fmt_currency(kpi.get("total_revenue"), short=True),
        "subtitle": f"Target: {fmt_currency(kpi.get('total_target'), short=True)}",
        "delta": d_rev,
        "delta_positive": d_rev_pos,
        "accent_color": COLORS["accent"],
        "icon": "💰",
    },
    {
        "title": "Achievement",
        "value": fmt_pct(kpi.get("achievement_pct")),
        "subtitle": "vs Target",
        "accent_color": achievement_color(kpi.get("achievement_pct")),
        "icon": "🎯",
    },
    {
        "title": "Units Sold",
        "value": fmt_number(kpi.get("total_units")),
        "delta": d_units,
        "delta_positive": d_units_pos,
        "accent_color": COLORS["chart"][2],
        "icon": "📦",
    },
    {
        "title": "Active Clients",
        "value": fmt_number(kpi.get("total_clients")),
        "delta": d_clients,
        "delta_positive": d_clients_pos,
        "accent_color": COLORS["chart"][3],
        "icon": "🏪",
    },
    {
        "title": "Transactions",
        "value": fmt_number(kpi.get("total_transactions")),
        "accent_color": COLORS["chart"][4],
        "icon": "🔄",
    },
])

st.markdown('<div class="spacer-md"></div>', unsafe_allow_html=True)


# ---- Gauge + Monthly Trend -------------------------------------------------
col_gauge, col_trend = st.columns([1, 3])

with col_gauge:
    achievement_gauge(
        pct=float(kpi.get("achievement_pct") or 0),
        title=f"Achievement — {period_label}",
        height=240,
    )
    # Gap fill KPI
    st.markdown('<div class="spacer-sm"></div>', unsafe_allow_html=True)
    kpi_card(
        title="Total Weight (Kg)",
        value=fmt_number(kpi.get("total_weight_kg")),
        icon="⚖️",
        accent_color=COLORS["chart"][1],
    )

with col_trend:
    trend_df = get_monthly_trend(year, regions, channels)
    revenue_vs_target_chart(
        trend_df,
        x_col="month",
        x_label_fn=month_name,
        title=f"Monthly Revenue vs Target — {year}",
        height=280,
    )


st.markdown('<div class="spacer-md"></div>', unsafe_allow_html=True)


# ---- YoY Trend + Category breakdown ----------------------------------------
render_section_header("Year-over-Year Comparison",
                       f"Current year {year} vs prior year {year-1}")

col_yoy, col_cat = st.columns([3, 2])

with col_yoy:
    yoy_df = get_ytd_vs_prior_year(year)
    trend_line_chart(
        yoy_df,
        x_col="month",
        y_cols=["cy_revenue", "py_revenue"],
        names=[f"CY {year}", f"PY {year-1}"],
        colors=[COLORS["accent"], COLORS["chart"][2]],
        title="Revenue CY vs PY — Monthly",
        x_label_fn=month_name,
        height=280,
    )

with col_cat:
    cat_df = get_category_performance(year, month)
    donut_chart(
        cat_df,
        label_col="product_category",
        value_col="revenue",
        title="Revenue by Category",
        height=280,
    )


st.markdown('<div class="spacer-md"></div>', unsafe_allow_html=True)


# ---- Top Regions -----------------------------------------------------------
render_section_header("Regional Snapshot", "Top performing regions this period")

col_bar, col_tbl = st.columns([2, 3])

with col_bar:
    top_reg = get_top_regions(year, month, limit=10)
    horizontal_bar_chart(
        top_reg.sort_values("revenue"),
        y_col="region",
        x_col="revenue",
        color_col="achievement_pct",
        title="Revenue by Region",
        height=300,
        max_rows=10,
    )

with col_tbl:
    render_ranking_table(
        get_top_regions(year, month, limit=10),
        name_col="region",
        title="Regional Ranking",
        rank_col=True,
    )


# ---- Footer ----------------------------------------------------------------
st.markdown(
    f'<div style="text-align:center; color:{COLORS["neutral"]}; font-size:0.7rem; '
    f'margin-top:2rem; border-top:1px solid {COLORS["border"]}; padding-top:0.75rem;">'
    f'Sales Analytics Platform · Data as of serving.db · Cached 5 min</div>',
    unsafe_allow_html=True,
)
