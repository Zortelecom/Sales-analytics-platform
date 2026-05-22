"""
reporting/pages/1_Executive_Overview.py
Executive Overview — top-level KPIs, achievement gauge, YoY trend.
Suitable for: Weekly briefing banner, Monthly/Quarterly/Annual reviews.


"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import reporting._bootstrap  # noqa: F401

import streamlit as st
from reporting.utils.filters import render_sidebar_filters, MONTH_NAMES
from reporting.utils.formatters import (
    fmt_currency, fmt_pct, fmt_number, fmt_delta, _is_null, month_name, achievement_color
)
from reporting.utils.queries import (
    get_executive_kpis,
    get_monthly_trend,
    get_ytd_vs_prior_year,
    get_top_regions,
    get_category_performance,
)
from reporting.components.kpi_cards import render_kpi_row, render_section_header, render_page_header
from reporting.components.charts import (
    revenue_vs_target_chart,
    achievement_gauge,
    trend_line_chart,
    donut_chart,
    horizontal_bar_chart,
)
from reporting.components.ranking_table import render_ranking_table
from reporting.config import COLORS


# ---- Filters ---------------------------------------------------------------
f = render_sidebar_filters(show_month=True, show_region=True, show_channel=True)
year     = f["year"]
month    = f["month"]
regions  = f["regions"]
channels = f["channels"]
mt       = f["meeting_type"]


# ---- Page Header -----------------------------------------------------------
period_label = (
    f"Week of {MONTH_NAMES.get(month,'')} {year}" if mt == "Weekly" and month else
    f"{MONTH_NAMES.get(month,'')} {year}" if month else
    f"Q{f['quarter']} {year}" if f["quarter"] else
    f"Full Year {year}"
)

render_page_header("Executive Overview", period_label, mt)


# ---- KPI Data --------------------------------------------------------------
with st.spinner("Loading KPIs…"):
    kpi_df = get_executive_kpis(year, month, regions, channels)
    kpi    = kpi_df.iloc[0] if not kpi_df.empty else {}

with st.spinner("Loading prior-year comparison…"):
    kpi_py_df = get_executive_kpis(year - 1, month, regions, channels)
    kpi_py    = kpi_py_df.iloc[0] if not kpi_py_df.empty else {}


def _delta(key: str) -> tuple[str, bool]:
    """Compute YoY delta string + positivity flag.

    Uses _is_null() before coercing to float so a genuine None/NaN prior-year
    value returns ("—", True) rather than silently evaluating as 0 and
    suppressing the delta indicator.
    """
    curr_raw = kpi.get(key)
    prev_raw = kpi_py.get(key)

    if _is_null(curr_raw) or _is_null(prev_raw):
        return "", True

    curr = float(curr_raw)
    prev = float(prev_raw)
    if prev <= 0:
        return "", True
    pct = (curr - prev) / prev * 100
    sign = "▲" if pct >= 0 else "▼"
    return f"{sign} {abs(pct):.1f}% vs PY", pct >= 0


# ---- Top KPI Row -----------------------------------------------------------
d_rev,     d_rev_pos     = _delta("total_revenue")
d_units,   d_units_pos   = _delta("total_units")
d_clients, d_clients_pos = _delta("total_clients")

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
        "title": "Total Weight (Kg)",
        "value": fmt_number(kpi.get("total_weight_kg")),
        "accent_color": COLORS["chart"][1],
        "icon": "⚖️",
    }
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
    st.markdown('<div class="spacer-sm"></div>', unsafe_allow_html=True)

with col_trend:
    with st.spinner("Loading monthly trend…"):
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
    with st.spinner("Loading YoY data…"):
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
    with st.spinner("Loading category breakdown…"):
        cat_df = get_category_performance(year, month, region=None, channels=channels)
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

col_bar, col_tbl = st.columns([3, 2])

with st.spinner("Loading regional data…"):
    top_reg = get_top_regions(year, month, channels=channels, limit=10)

with col_bar:
    if not top_reg.empty:
        horizontal_bar_chart(
            top_reg.sort_values("revenue"),
            y_col="region",
            x_col="revenue",
            color_col="achievement_pct",
            title="Revenue by Region",
            height=max(300, len(top_reg) * 40),
            max_rows=10,
        )
    else:
        st.info("No regional data available")

with col_tbl:
    render_ranking_table(
        top_reg,
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