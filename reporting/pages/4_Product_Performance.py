"""
reporting/pages/4_Product_Performance.py
Product Performance — Category, subcategory, SKU ranking, and Innovation spotlight.

Fixes applied:
  - sys.path.insert restored before bootstrap import (both are required: insert
    makes 'reporting' importable; _bootstrap provides idempotency for other entry points)
  - Inline SQL for cat_trend moved to queries.get_category_monthly_trend()
  - Category filter now works correctly: get_category_performance no longer
    applies UPPER(), so case matches between the selectbox and the filter param
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
    fmt_currency, fmt_pct, fmt_number, achievement_color, month_name
)
from reporting.utils.queries import (
    get_category_performance,
    get_product_ranking,
    get_innovation_performance,
    get_innovation_trend,
    get_category_monthly_trend,
)
from reporting.components.kpi_cards import render_kpi_row, render_section_header, kpi_card
from reporting.components.charts import (
    donut_chart,
    horizontal_bar_chart,
    trend_line_chart,
    revenue_vs_target_chart,
)
from reporting.components.ranking_table import render_ranking_table
from reporting.config import COLORS


# ---- Filters ---------------------------------------------------------------
f = render_sidebar_filters(show_region=True, show_channel=False, show_category=True)
year       = f["year"]
month      = f["month"]
regions    = f["regions"]
categories = f["categories"]
mt         = f["meeting_type"]

period_label = (
    f"{MONTH_NAMES.get(month,'')} {year}" if month else
    f"Q{f['quarter']} {year}" if f["quarter"] else f"Full Year {year}"
)
region_arg = regions[0] if (regions and len(regions) == 1) else None
cat_arg    = categories[0] if (categories and len(categories) == 1) else None

# ---- Page Header -----------------------------------------------------------
st.markdown(
    f"""
    <div style="display:flex; align-items:center; justify-content:space-between;
                margin-bottom:1.2rem; border-bottom:1px solid {COLORS['border']};
                padding-bottom:0.8rem;">
        <div>
            <h1 style="margin:0; font-size:1.6rem; font-weight:700;">Product Performance</h1>
            <p style="margin:0; color:{COLORS['text_secondary']}; font-size:0.85rem;">
                {period_label} &nbsp;·&nbsp; {mt} Report
            </p>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ---- Data ------------------------------------------------------------------
cat_df   = get_category_performance(year, month, region_arg)
prod_df  = get_product_ranking(year, month, cat_arg, region_arg, limit=30)
innov_df = get_innovation_performance(year, month)

if cat_df.empty:
    st.warning("No product data for the selected period.")
    st.stop()

total_rev = cat_df["revenue"].sum()
total_tgt = cat_df["target"].sum()
total_ach = round(total_rev / total_tgt * 100, 2) if total_tgt > 0 else None
top_cat   = cat_df.iloc[0] if not cat_df.empty else {}

# Innovation split
innov_rev   = innov_df[innov_df["is_innovation_product"] == True]["revenue"].sum() if not innov_df.empty else 0
innov_share = round(innov_rev / total_rev * 100, 1) if total_rev > 0 else None


# ---- KPIs ------------------------------------------------------------------
render_kpi_row([
    {"title": "Total Revenue", "value": fmt_currency(total_rev, short=True),
     "subtitle": f"Target: {fmt_currency(total_tgt, short=True)}", "icon": "💰"},
    {"title": "Achievement", "value": fmt_pct(total_ach),
     "accent_color": achievement_color(total_ach), "icon": "🎯"},
    {"title": "Top Category", "value": str(top_cat.get("product_category", "—")),
     "subtitle": fmt_currency(top_cat.get("revenue"), short=True),
     "accent_color": COLORS["success"], "icon": "🏆"},
    {"title": "Innovation Share", "value": fmt_pct(innov_share),
     "subtitle": fmt_currency(innov_rev, short=True),
     "accent_color": COLORS["chart"][3], "icon": "✨"},
    {"title": "SKUs Sold", "value": fmt_number(prod_df["sku"].nunique()) if not prod_df.empty else "—",
     "accent_color": COLORS["chart"][2], "icon": "🏷️"},
])

st.markdown('<div class="spacer-md"></div>', unsafe_allow_html=True)


# ---- Tabs ------------------------------------------------------------------
tab1, tab2, tab3, tab4 = st.tabs([
    "📊 Categories",
    "🏷️ Product Ranking",
    "✨ Innovation",
    "📈 Trends",
])


# ====== TAB 1: Categories ===================================================
with tab1:
    col_donut, col_bar = st.columns([1, 2])

    with col_donut:
        donut_chart(cat_df, "product_category", "revenue",
                   title="Revenue Share by Category", height=300)

    with col_bar:
        horizontal_bar_chart(
            cat_df.sort_values("revenue"),
            y_col="product_category",
            x_col="revenue",
            color_col="achievement_pct",
            title="Category Revenue vs Target",
            height=300,
        )

    st.markdown('<div class="spacer-sm"></div>', unsafe_allow_html=True)
    render_ranking_table(
        cat_df.sort_values("revenue", ascending=False),
        name_col="product_category",
        title="Category Performance Table",
        extra_cols=["units_sold", "weight_kg"],
    )


# ====== TAB 2: Product Ranking ==============================================
with tab2:
    # Category list comes from get_category_performance which no longer applies
    # UPPER(), so case matches the values stored in v_sales_base.
    all_cats = ["All Categories"] + cat_df["product_category"].tolist()
    sel_cat  = st.selectbox("Filter by Category", all_cats, key="prod_cat_filter")

    cat_filter    = None if sel_cat == "All Categories" else sel_cat
    filtered_prod = get_product_ranking(year, month, cat_filter, region_arg, limit=30)

    if filtered_prod.empty:
        st.info("No product data.")
    else:
        col_top, col_bot = st.columns(2)

        with col_top:
            render_section_header("Top Products by Revenue")
            top_prods = filtered_prod.head(15).copy()
            horizontal_bar_chart(
                top_prods.sort_values("revenue"),
                y_col="product_name",
                x_col="revenue",
                title="",
                height=max(260, len(top_prods) * 30),
            )

        with col_bot:
            render_section_header("Product Detail Table")
            rows_html = ""
            for i, row in filtered_prod.head(20).iterrows():
                innov_badge = (
                    f'<span style="background:{COLORS["chart"][3]}; color:#000; '
                    f'border-radius:3px; padding:0.1rem 0.3rem; font-size:0.68rem; '
                    f'font-weight:700;">NEW</span>'
                    if row.get("is_innovation_product") is True else ""
                )
                rows_html += f"""
                <tr style="border-bottom:1px solid {COLORS['border']};">
                    <td style="color:{COLORS['text_primary']}; padding:0.3rem 0.25rem;">
                        {row.get('product_name','')} {innov_badge}
                    </td>
                    <td style="color:{COLORS['text_secondary']}; font-size:0.78rem;">{row.get('product_subcategory','')}</td>
                    <td style="color:{COLORS['text_primary']}; text-align:right; font-weight:600;">
                        {fmt_currency(row.get('revenue'), short=True)}</td>
                    <td style="color:{COLORS['text_secondary']}; text-align:right;">
                        {fmt_number(row.get('units_sold'))}</td>
                </tr>
                """
            st.markdown(f"""
            <div style="overflow-y:auto; max-height:400px;">
            <table style="width:100%; border-collapse:collapse; font-size:0.83rem;">
                <thead><tr style="background:{COLORS['bg_card_alt']}; color:{COLORS['text_secondary']};
                           font-size:0.72rem; text-transform:uppercase;">
                    <th style="text-align:left; padding:0.4rem 0.25rem;">Product</th>
                    <th style="text-align:left; padding:0.4rem 0.25rem;">Subcategory</th>
                    <th style="text-align:right; padding:0.4rem 0.25rem;">Revenue</th>
                    <th style="text-align:right; padding:0.4rem 0.25rem;">Units</th>
                </tr></thead>
                <tbody>{rows_html}</tbody>
            </table>
            </div>
            """, unsafe_allow_html=True)


# ====== TAB 3: Innovation Spotlight =========================================
with tab3:
    render_section_header("Innovation Product Performance",
                           "NEW-flagged products vs standard portfolio")

    if innov_df.empty:
        st.info("No data.")
    else:
        innov_yes = innov_df[innov_df["is_innovation_product"] == True]
        innov_no  = innov_df[innov_df["is_innovation_product"] == False]

        col_a, col_b = st.columns(2)
        with col_a:
            render_kpi_row([
                {"title": "✨ Innovation Revenue", "value": fmt_currency(innov_yes["revenue"].sum(), short=True),
                 "subtitle": f"Share: {fmt_pct(innov_share)}",
                 "accent_color": COLORS["chart"][3], "icon": "✨"},
            ])
            st.markdown('<div class="spacer-sm"></div>', unsafe_allow_html=True)
            donut_chart(
                innov_df.groupby("is_innovation_product")["revenue"].sum().reset_index()
                .assign(label=lambda d: d["is_innovation_product"].map({True:"Innovation", False:"Standard"})),
                label_col="label",
                value_col="revenue",
                title="Innovation vs Standard",
                height=250,
            )

        with col_b:
            render_section_header("Innovation Revenue by Category")
            if not innov_yes.empty:
                donut_chart(innov_yes, "product_category", "revenue",
                           title="Innovation SKUs — Category Mix", height=250)

        st.markdown('<div class="spacer-sm"></div>', unsafe_allow_html=True)
        render_section_header("Innovation Performance by Category")
        render_ranking_table(
            innov_yes.sort_values("revenue", ascending=False) if not innov_yes.empty else pd.DataFrame(),
            name_col="product_category",
            title="",
            rank_col=True,
        )


# ====== TAB 4: Trends =======================================================
with tab4:
    innov_trend = get_innovation_trend(year)

    if not innov_trend.empty:
        innov_trend_pivot = innov_trend.pivot_table(
            index="month", columns="is_innovation_product",
            values="revenue", aggfunc="sum"
        ).reset_index()
        innov_trend_pivot.columns = ["month"] + [
            "Innovation" if c else "Standard"
            for c in innov_trend_pivot.columns[1:]
        ]
        y_cols = [c for c in innov_trend_pivot.columns if c != "month"]
        trend_line_chart(
            innov_trend_pivot,
            x_col="month",
            y_cols=y_cols,
            names=y_cols,
            colors=[COLORS["chart"][3], COLORS["chart"][2]],
            title=f"Innovation vs Standard Revenue Trend — {year}",
            x_label_fn=month_name,
            height=300,
        )

    st.markdown('<div class="spacer-sm"></div>', unsafe_allow_html=True)
    render_section_header("Category Monthly Trend")

    # Inline SQL extracted to queries.get_category_monthly_trend()
    cat_trend = get_category_monthly_trend(year)

    if not cat_trend.empty:
        cats     = cat_trend["product_category"].unique()
        sel_cats = st.multiselect("Categories to display", list(cats), default=list(cats[:4]),
                                   key="cat_trend_select")
        if sel_cats:
            pivot_cat = (
                cat_trend[cat_trend["product_category"].isin(sel_cats)]
                .pivot_table(index="month", columns="product_category", values="revenue", aggfunc="sum")
                .reset_index()
                .fillna(0)
            )
            y_cols_cat = [c for c in pivot_cat.columns if c != "month"]
            trend_line_chart(
                pivot_cat,
                x_col="month",
                y_cols=y_cols_cat,
                names=y_cols_cat,
                colors=COLORS["chart"],
                title="Monthly Revenue by Category",
                x_label_fn=month_name,
                height=300,
            )
