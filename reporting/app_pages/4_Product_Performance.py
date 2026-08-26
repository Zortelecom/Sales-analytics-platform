"""
reporting/pages/4_Product_Performance.py
Product Performance — Category, subcategory, SKU ranking, and Innovation spotlight.

"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import reporting._bootstrap  # noqa: F401

import streamlit as st
import pandas as pd
from reporting.utils.filters import period_label, render_sidebar_filters
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
from reporting.components.kpi_cards import render_kpi_row, render_section_header, render_page_header
from reporting.components.charts import (
    donut_chart,
    horizontal_bar_chart,
    trend_line_chart,
    revenue_vs_target_chart,
)
from reporting.components.ranking_table import render_ranking_table
from reporting.config import COLORS
from reporting.utils.markup import html_table


# ---- Filters ---------------------------------------------------------------
f = render_sidebar_filters(show_region=True, show_subregion=True,
                           show_channel=False, show_category=True)
period      = f["period"]
regions     = f["regions"]
subregions  = f["subregions"]
categories  = f["categories"]
mt          = f["meeting_type"]

# region_arg / cat_arg are GONE: they passed the filter through only when
# EXACTLY ONE value was selected and dropped it silently for two or more.
label = period_label(f)
render_page_header("Product Performance", label, mt)


# ---- Data ------------------------------------------------------------------
with st.spinner("Loading product data…"):
    cat_df   = get_category_performance(period, None, regions, subregions=subregions)
    prod_df  = get_product_ranking(period, None, categories, regions, limit=30,
                                   subregions=subregions)
    innov_df = get_innovation_performance(period, None, regions, subregions)

if cat_df.empty:
    st.warning("No product data for the selected period.")
    st.stop()

total_rev = cat_df["revenue"].sum()
total_tgt = cat_df["target"].sum()
total_ach = round(total_rev / total_tgt * 100, 2) if total_tgt > 0 else None
top_cat   = cat_df.iloc[0] if not cat_df.empty else {}

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
    all_cats = ["All Categories"] + cat_df["product_category"].tolist()
    sel_cat  = st.selectbox("Filter by Category", all_cats, key="prod_cat_filter")

    cat_filter    = None if sel_cat == "All Categories" else sel_cat
    with st.spinner("Loading product ranking…"):
        filtered_prod = get_product_ranking(period, None, cat_filter, regions,
                                            limit=30, subregions=subregions)

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
            # html_table, NOT st.markdown. rows_html is built inside a `for`
            # inside a `with`, so every line arrives indented past four spaces
            # and the rows are separated by blank lines -- CommonMark reads the
            # first as a code block and the second as the end of the HTML
            # block, which is why the raw <tr> tags were rendering on screen.
            html_table(
                headers_html=(
                    '<th style="text-align:left; padding:0.4rem 0.25rem;">Product</th>'
                    '<th style="text-align:left; padding:0.4rem 0.25rem;">Subcategory</th>'
                    '<th style="text-align:right; padding:0.4rem 0.25rem;">Revenue</th>'
                    '<th style="text-align:right; padding:0.4rem 0.25rem;">Units</th>'
                ),
                rows_html=rows_html,
                colors=COLORS,
                max_height="400px",
            )


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
    innov_trend = get_innovation_trend(period.whole(), regions, subregions)

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
            title=f"Innovation vs Standard Revenue Trend — {period.label}",
            x_label_fn=month_name,
            height=300,
        )

    st.markdown('<div class="spacer-sm"></div>', unsafe_allow_html=True)
    render_section_header("Category Monthly Trend")

    cat_trend = get_category_monthly_trend(period.whole(), regions, subregions,
                                           categories)

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