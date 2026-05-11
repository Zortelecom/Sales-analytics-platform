"""
reporting/pages/5_Time_Intelligence.py
Time Intelligence — WoW, Same Month Last Year, QoQ, Seasonality heatmap.

Fixes applied:
  - sys.path.insert restored before bootstrap import (both are required: insert
    makes 'reporting' importable; _bootstrap provides idempotency for other entry points)
  - st.stop() inside tabs replaced with conditional rendering — previously
    StopException was aborting the entire page render, blanking all other tabs
  - WoW achievement colour now uses formatters.wow_color() with growth-appropriate
    thresholds instead of misusing achievement_color(pct + 100)
  - All three inline f-string SQL queries replaced with named query functions:
      get_region_weekly(), get_region_yoy(), get_category_month_heatmap()
    (also eliminates the f-string SQL injection / cache bypass issues)
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
    fmt_currency, fmt_pct, fmt_number, achievement_color, month_name,
    fmt_delta, wow_color,
)
from reporting.utils.queries import (
    get_weekly_performance,
    get_week_over_week,
    get_same_month_last_year,
    get_seasonality_heatmap,
    get_quarterly_summary,
    get_monthly_trend,
    get_ytd_vs_prior_year,
    get_region_weekly,
    get_region_yoy,
    get_category_month_heatmap,
)
from reporting.components.kpi_cards import render_kpi_row, render_section_header, kpi_card
from reporting.components.charts import (
    waterfall_chart,
    trend_line_chart,
    heatmap_chart,
    revenue_vs_target_chart,
    horizontal_bar_chart,
)
from reporting.components.ranking_table import render_comparison_table, render_ranking_table
from reporting.config import COLORS


# ---- Filters ---------------------------------------------------------------
f = render_sidebar_filters(show_region=True, show_channel=False)
year    = f["year"]
month   = f["month"]
regions = f["regions"]
mt      = f["meeting_type"]

region_arg = regions[0] if (regions and len(regions) == 1) else None
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
            <h1 style="margin:0; font-size:1.6rem; font-weight:700;">Time Intelligence</h1>
            <p style="margin:0; color:{COLORS['text_secondary']}; font-size:0.85rem;">
                {period_label} &nbsp;·&nbsp; {mt} Report
            </p>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ============================================================================
# TABS
# ============================================================================
tab_wow, tab_smly, tab_qtd, tab_annual, tab_heat = st.tabs([
    "📅 Week-over-Week",
    "📆 Same Month Last Year",
    "🗓️ Quarterly View",
    "📈 Annual Trend",
    "🌡️ Seasonality",
])


# ====== WEEK-OVER-WEEK ======================================================
with tab_wow:
    # st.stop() replaced with conditional rendering — st.stop() inside a tab
    # aborts the entire page script, blanking all remaining tabs.
    if not month:
        st.info("👈 Select a month in the sidebar to see week-over-week analysis.")
    else:
        render_section_header(
            f"Week-over-Week — {MONTH_NAMES[month]} {year}",
            "Revenue progression within the selected month"
        )

        wow_df  = get_week_over_week(year, month)
        week_df = get_weekly_performance(year, month, region_arg)

        if wow_df.empty:
            st.info("No weekly data available.")
        else:
            best_wk  = wow_df.loc[wow_df["revenue"].idxmax()]
            latest   = wow_df.iloc[-1]
            prev_wow = latest.get("wow_pct")

            render_kpi_row([
                {"title": "Best Week Revenue", "value": fmt_currency(best_wk["revenue"], short=True),
                 "subtitle": f"Week {int(best_wk['week_of_month'])}",
                 "accent_color": COLORS["success"], "icon": "🏆"},
                {"title": "Latest Week", "value": fmt_currency(latest["revenue"], short=True),
                 "subtitle": f"Week {int(latest['week_of_month'])}",
                 "accent_color": COLORS["accent"], "icon": "📅"},
                {
                    "title": "WoW Change",
                    "value": fmt_pct(prev_wow),
                    "subtitle": "vs previous week",
                    # wow_color() uses growth thresholds (≥5%/≥0%/≥-5%/else)
                    # instead of misusing achievement_color(pct + 100)
                    "accent_color": wow_color(prev_wow),
                    "icon": "📈" if (prev_wow or 0) >= 0 else "📉",
                    "delta_positive": (prev_wow or 0) >= 0,
                },
                {"title": "Month Total", "value": fmt_currency(wow_df["revenue"].sum(), short=True),
                 "accent_color": COLORS["chart"][2], "icon": "💰"},
            ])

            st.markdown('<div class="spacer-sm"></div>', unsafe_allow_html=True)

            col_wf, col_bar = st.columns([3, 2])
            with col_wf:
                waterfall_chart(
                    wow_df,
                    x_col="week_of_month",
                    value_col="revenue",
                    title=f"Weekly Revenue — {MONTH_NAMES[month]} {year}",
                    height=300,
                )
            with col_bar:
                st.markdown(
                    f'<h4 style="color:{COLORS["text_primary"]}; font-size:0.95rem; '
                    f'margin:0.5rem 0;">Weekly Breakdown</h4>',
                    unsafe_allow_html=True,
                )
                rows = ""
                for _, r in wow_df.iterrows():
                    pct = r.get("wow_pct")
                    pct_color = wow_color(pct)
                    pct_str = fmt_pct(pct) if pct is not None else "—"
                    rows += f"""
                    <tr style="border-bottom:1px solid {COLORS['border']};">
                        <td style="color:{COLORS['text_secondary']}; padding:0.35rem 0.25rem;">W{int(r['week_of_month'])}</td>
                        <td style="color:{COLORS['text_primary']}; font-weight:600; text-align:right;">{fmt_currency(r['revenue'], short=True)}</td>
                        <td style="color:{pct_color}; text-align:center;">{pct_str}</td>
                        <td style="color:{COLORS['text_secondary']}; text-align:right;">{fmt_number(r.get('units_sold'))}</td>
                    </tr>
                    """
                st.markdown(f"""
                <table style="width:100%; border-collapse:collapse; font-size:0.83rem;">
                    <thead><tr style="background:{COLORS['bg_card_alt']}; color:{COLORS['text_secondary']};
                               font-size:0.72rem; text-transform:uppercase;">
                        <th style="text-align:left;">Week</th>
                        <th style="text-align:right;">Revenue</th>
                        <th style="text-align:center;">WoW %</th>
                        <th style="text-align:right;">Units</th>
                    </tr></thead>
                    <tbody>{rows}</tbody>
                </table>
                """, unsafe_allow_html=True)

            # By-region weekly breakdown
            if region_arg is None and not week_df.empty:
                st.markdown('<div class="spacer-sm"></div>', unsafe_allow_html=True)
                render_section_header("Weekly by Region")
                # Inline SQL replaced with get_region_weekly()
                region_weekly = get_region_weekly(year, month)
                if not region_weekly.empty:
                    pivot_rw = region_weekly.pivot_table(
                        index="week_of_month", columns="region", values="revenue", aggfunc="sum"
                    ).fillna(0).reset_index()
                    y_cols = [c for c in pivot_rw.columns if c != "week_of_month"]
                    trend_line_chart(
                        pivot_rw,
                        x_col="week_of_month",
                        y_cols=y_cols,
                        names=y_cols,
                        colors=COLORS["chart"],
                        title="Weekly Revenue by Region",
                        x_label_fn=lambda w: f"W{int(w)}",
                        height=280,
                    )


# ====== SAME MONTH LAST YEAR ================================================
with tab_smly:
    # st.stop() replaced with conditional rendering
    if not month:
        st.info("👈 Select a month to compare with the same month last year.")
    else:
        render_section_header(
            f"Same Month Last Year — {MONTH_NAMES[month]}",
            f"{MONTH_NAMES[month]} {year} vs {MONTH_NAMES[month]} {year-1}"
        )

        smly_df = get_same_month_last_year(year, month, region_arg)

        if smly_df.empty:
            st.info("No comparison data.")
        else:
            cy_row = smly_df[smly_df["period"] == "Current Year"].iloc[0] if len(smly_df) > 0 else {}
            py_row = smly_df[smly_df["period"] == "Prior Year"].iloc[0] if len(smly_df) > 1 else {}

            cy_rev = float(cy_row.get("revenue", 0) or 0)
            py_rev = float(py_row.get("revenue", 0) or 0)
            rev_delta = (cy_rev - py_rev) / py_rev * 100 if py_rev > 0 else None

            cy_units = float(cy_row.get("units_sold", 0) or 0)
            py_units = float(py_row.get("units_sold", 0) or 0)
            units_delta = (cy_units - py_units) / py_units * 100 if py_units > 0 else None

            d_pos = (rev_delta or 0) >= 0

            render_kpi_row([
                {"title": f"CY {MONTH_NAMES[month]} {year}", "value": fmt_currency(cy_rev, short=True),
                 "accent_color": COLORS["accent"], "icon": "📅"},
                {"title": f"PY {MONTH_NAMES[month]} {year-1}", "value": fmt_currency(py_rev, short=True),
                 "accent_color": COLORS["chart"][2], "icon": "📆"},
                {"title": "Revenue Growth", "value": fmt_pct(rev_delta),
                 "delta_positive": d_pos,
                 "accent_color": COLORS["success"] if d_pos else COLORS["danger"],
                 "icon": "📈" if d_pos else "📉"},
                {"title": "Units Growth", "value": fmt_pct(units_delta),
                 "delta_positive": (units_delta or 0) >= 0,
                 "accent_color": COLORS["success"] if (units_delta or 0) >= 0 else COLORS["danger"],
                 "icon": "📦"},
            ])

            st.markdown('<div class="spacer-sm"></div>', unsafe_allow_html=True)

            render_section_header("Month-by-Month: CY vs PY")
            yoy_df = get_ytd_vs_prior_year(year)
            trend_line_chart(
                yoy_df,
                x_col="month",
                y_cols=["cy_revenue", "py_revenue"],
                names=[f"CY {year}", f"PY {year-1}"],
                colors=[COLORS["accent"], COLORS["chart"][2]],
                title=f"Monthly Revenue — {year} vs {year-1}",
                x_label_fn=month_name,
                height=280,
            )

            st.markdown('<div class="spacer-sm"></div>', unsafe_allow_html=True)
            render_section_header(f"By Region — {MONTH_NAMES[month]} CY vs PY")
            # Inline SQL replaced with get_region_yoy()
            region_yoy = get_region_yoy(year, month)
            if not region_yoy.empty:
                region_yoy["yoy_pct"] = region_yoy.apply(
                    lambda r: round((r.cy_revenue - r.py_revenue) / r.py_revenue * 100, 2)
                    if r.py_revenue > 0 else None, axis=1
                )
                render_comparison_table(
                    region_yoy,
                    group_col="region",
                    value_col="cy_revenue",
                    compare_col="py_revenue",
                    pct_change_col="yoy_pct",
                    title=f"Regional SMLY — {MONTH_NAMES[month]}",
                )


# ====== QUARTERLY VIEW ======================================================
with tab_qtd:
    render_section_header("Quarterly Performance", "Revenue and achievement by quarter")

    q_df = get_quarterly_summary(year, region_arg)

    if q_df.empty:
        st.info("No quarterly data.")
    else:
        q_totals = (
            q_df.groupby("quarter")
            .agg(revenue=("revenue","sum"), target=("target","sum"),
                 units_sold=("units_sold","sum"))
            .reset_index()
        )
        q_totals["achievement_pct"] = q_totals.apply(
            lambda r: round(r.revenue/r.target*100,2) if r.target > 0 else None, axis=1
        )

        q_cols = st.columns(4)
        for i, q in enumerate(range(1, 5)):
            q_row = q_totals[q_totals["quarter"] == q]
            with q_cols[i]:
                if not q_row.empty:
                    r = q_row.iloc[0]
                    ach = r.get("achievement_pct")
                    color = achievement_color(ach)
                    st.markdown(
                        f"""
                        <div style="background:{COLORS['bg_card']};
                                    border:1px solid {COLORS['border']};
                                    border-top:3px solid {color};
                                    border-radius:8px; padding:1rem;
                                    text-align:center;">
                            <div style="color:{COLORS['text_secondary']}; font-size:0.75rem;
                                        text-transform:uppercase; letter-spacing:0.08em;">
                                Q{q} {year}
                            </div>
                            <div style="color:{COLORS['text_primary']}; font-size:1.3rem;
                                        font-weight:700; margin:0.3rem 0;">
                                {fmt_currency(r.revenue, short=True)}
                            </div>
                            <div style="color:{color}; font-size:1rem; font-weight:600;">
                                {fmt_pct(ach)}
                            </div>
                            <div style="color:{COLORS['text_secondary']}; font-size:0.72rem;">
                                vs {fmt_currency(r.target, short=True)}
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f'<div style="background:{COLORS["bg_card"]}; border:1px solid {COLORS["border"]}; '
                        f'border-radius:8px; padding:1rem; text-align:center; '
                        f'color:{COLORS["neutral"]};">Q{q} — No data</div>',
                        unsafe_allow_html=True,
                    )

        st.markdown('<div class="spacer-md"></div>', unsafe_allow_html=True)

        revenue_vs_target_chart(
            q_totals,
            x_col="quarter",
            x_label_fn=lambda q: f"Q{int(q)}",
            title=f"Quarterly Revenue vs Target — {year}",
            height=280,
        )

        st.markdown('<div class="spacer-sm"></div>', unsafe_allow_html=True)
        if region_arg is None:
            q_pivot = q_df.pivot_table(
                index="region", columns="quarter", values="achievement_pct", aggfunc="mean"
            ).round(1)
            if not q_pivot.empty:
                render_section_header("Achievement % by Region × Quarter")
                rows = ""
                for reg, row in q_pivot.iterrows():
                    cells = "".join(
                        f'<td style="color:{achievement_color(v if pd.notna(v) else None)};'
                        f' text-align:center; padding:0.35rem 0.5rem; font-weight:600;">'
                        f'{fmt_pct(v) if pd.notna(v) else "—"}</td>'
                        for v in row
                    )
                    rows += f"""
                    <tr style="border-bottom:1px solid {COLORS['border']};">
                        <td style="color:{COLORS['text_primary']}; padding:0.35rem 0.5rem;">{reg}</td>
                        {cells}
                    </tr>
                    """
                headers = "".join(f'<th style="text-align:center;">Q{int(q)}</th>'
                                  for q in q_pivot.columns)
                st.markdown(f"""
                <table style="width:100%; border-collapse:collapse; font-size:0.83rem;">
                    <thead><tr style="background:{COLORS['bg_card_alt']}; color:{COLORS['text_secondary']};
                               font-size:0.72rem; text-transform:uppercase;">
                        <th style="text-align:left; padding:0.4rem 0.5rem;">Region</th>
                        {headers}
                    </tr></thead>
                    <tbody>{rows}</tbody>
                </table>
                """, unsafe_allow_html=True)


# ====== ANNUAL TREND ========================================================
with tab_annual:
    render_section_header(f"Annual View — {year}", "Full-year monthly trend and cumulative performance")

    annual_df = get_monthly_trend(year)
    if annual_df.empty:
        st.info("No annual data.")
    else:
        annual_df = annual_df.copy()
        annual_df["cum_revenue"] = annual_df["revenue"].cumsum()
        annual_df["cum_target"]  = annual_df["target"].cumsum()

        col_trend1, col_trend2 = st.columns(2)
        with col_trend1:
            revenue_vs_target_chart(
                annual_df,
                x_col="month",
                x_label_fn=month_name,
                title=f"Monthly Revenue vs Target — {year}",
                height=280,
            )
        with col_trend2:
            trend_line_chart(
                annual_df,
                x_col="month",
                y_cols=["cum_revenue", "cum_target"],
                names=["Cumulative Revenue", "Cumulative Target"],
                colors=[COLORS["accent"], COLORS["danger"]],
                title=f"YTD Cumulative — {year}",
                x_label_fn=month_name,
                height=280,
            )

        st.markdown('<div class="spacer-sm"></div>', unsafe_allow_html=True)

        yoy_df = get_ytd_vs_prior_year(year)
        col_yoy, col_yoy_tbl = st.columns([3, 2])
        with col_yoy:
            trend_line_chart(
                yoy_df,
                x_col="month",
                y_cols=["cy_revenue", "py_revenue"],
                names=[f"{year}", f"{year-1}"],
                colors=[COLORS["accent"], COLORS["chart"][2]],
                title=f"Revenue — {year} vs {year-1}",
                x_label_fn=month_name,
                height=260,
            )
        with col_yoy_tbl:
            render_comparison_table(
                yoy_df,
                group_col="month",
                value_col="cy_revenue",
                compare_col="py_revenue",
                pct_change_col="yoy_pct",
                title="Month-by-Month YoY",
            )


# ====== SEASONALITY HEATMAP =================================================
with tab_heat:
    render_section_header(
        "Seasonality Heatmap",
        "Revenue distribution by month × day of week"
    )

    heat_df = get_seasonality_heatmap(year, region_arg)

    if heat_df.empty:
        st.info("No seasonality data.")
    else:
        DAY_NAMES = {0:"Mon", 1:"Tue", 2:"Wed", 3:"Thu", 4:"Fri", 5:"Sat", 6:"Sun"}
        if "day_of_week" in heat_df.columns:
            heat_df["day_of_week"] = (
                heat_df["day_of_week"]
                .map(DAY_NAMES)
                .fillna(heat_df["day_of_week"].astype(str))
            )

        heatmap_chart(
            heat_df,
            x_col="month",
            y_col="day_of_week",
            value_col="revenue",
            title=f"Revenue Seasonality — {year}",
            height=300,
        )

        st.markdown('<div class="spacer-sm"></div>', unsafe_allow_html=True)

        render_section_header("Category × Month Revenue Matrix")
        # Inline SQL replaced with get_category_month_heatmap()
        cat_month = get_category_month_heatmap(year)
        if not cat_month.empty:
            heatmap_chart(
                cat_month,
                x_col="month",
                y_col="product_category",
                value_col="revenue",
                title=f"Category × Month Revenue — {year}",
                height=280,
            )
