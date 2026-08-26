"""
reporting/utils/queries.py
All SQL query builders for the reporting pages.
Returns pd.DataFrames via db.query().


If you add a query, keep using bare names and go through db.query().


TWO CONVENTIONS EVERY BUILDER NOW FOLLOWS
-----------------------------------------
1. PERIOD, NOT YEAR. The first argument is a `Period` (see
   reporting/utils/period.py) or a bare calendar year. A bare int keeps the old
   calendar behaviour, so an unmigrated page keeps working; a Period carries
   the fiscal basis, which cannot be expressed as `year = ?` because FY2026
   spans Oct-2025..Sep-2026.

2. SUBREGIONS ARE A FIRST-CLASS FILTER. Every builder that accepts `regions`
   now accepts `subregions`, and both accept a str or a list.

   This is the fix for "the subregion filter does nothing for admin and RBM".
   The sidebar has always RETURNED the selection; nothing consumed it. It
   appeared to work for supervisors only because rls.py rewrites the object
   underneath -- that is the security boundary doing its job, not the filter.
   An admin has no RLS predicate, so the selection had no path into the SQL at
   all. The filter and the boundary are now independent: RLS scopes the object,
   this scopes the selection, and a scoped user gets the intersection.

   `subregion` exists on v_sales_base, v_monthly_kpi, v_weekly_kpi and
   v_quarterly_kpi -- the same audit as SCOPE_COLUMNS in rls.py. Where it is
   only present after bi_views_rls_patch.sql (the KP-SD views), the builder
   ASKS first via db.object_columns() rather than assuming.
"""
from __future__ import annotations
import pandas as pd
from reporting.utils.db import object_columns, object_exists, query
from reporting.utils.period import Period


# =============================================================================
# EXECUTIVE OVERVIEW
# =============================================================================

def get_executive_kpis(year: Period | int, month: int | None = None,
                        regions: list[str] | None = None,
                        channels: list[str] | None = None,
                        subregions: list[str] | None = None) -> pd.DataFrame:
    """Top-level KPIs: revenue, target, achievement, units, weight, clients, transactions.
    active_clients drawn from v_sales_base to avoid cross-salesperson double-counting.
    """
    kpi_filters, kpi_params = _build_filters(year=year, month=month,
                                              regions=regions, subregions=subregions,
                                              channels=channels,
                                              prefix="WHERE")
    base_filters, base_params = _build_filters(year=year, month=month,
                                                regions=regions, subregions=subregions,
                                                channels=channels,
                                                prefix="WHERE",
                                                year_col="sale_year",
                                                month_col="sale_month")
    sql = f"""
    WITH kpi AS (
        SELECT
            SUM(revenue)           AS total_revenue,
            SUM(target)            AS total_target,
            CASE WHEN SUM(target)>0
                 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct,
            SUM(units_sold)        AS total_units,
            SUM(weight_kg)         AS total_weight_kg,
            SUM(transaction_count) AS total_transactions
        FROM v_monthly_kpi
        {kpi_filters}
    ),
    clients AS (
        SELECT COUNT(DISTINCT clientsd_id) AS total_clients
        FROM v_sales_base
        {base_filters}
    )
    SELECT k.*, c.total_clients
    FROM kpi k CROSS JOIN clients c
    """
    return query(sql, tuple(kpi_params + base_params))


def get_monthly_trend(year: Period | int, regions: list[str] | None = None,
                      channels: list[str] | None = None,
                      subregions: list[str] | None = None) -> pd.DataFrame:
    """Monthly revenue vs target trend for the year.

    Called with `period.whole()` from the pages: a trend chart shows the whole
    year regardless of which month is selected in the sidebar.

    ORDER BY is the period's own expression, not `month`. Under a fiscal basis
    the year opens in October, so ordering by the calendar month number draws
    January first and puts the opening quarter at the far right of the chart.
    """
    period = Period.coerce(year)
    filters, params = _build_filters(year=period, regions=regions,
                                     subregions=subregions, channels=channels,
                                     prefix="WHERE")
    sql = f"""
    SELECT
        month,
        SUM(revenue) AS revenue,
        SUM(target)  AS target,
        CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct
    FROM v_monthly_kpi
    {filters}
    GROUP BY month
    ORDER BY {period.order_expr('month')}
    """
    return query(sql, tuple(params))


def get_ytd_vs_prior_year(year: Period | int,
                          regions: list[str] | None = None,
                          subregions: list[str] | None = None,
                          channels: list[str] | None = None) -> pd.DataFrame:
    """YTD revenue current period vs the same period a year earlier, by month.

    `period.prior()` rather than `year - 1`: under a fiscal basis the prior
    period is FY2025 = Oct-2023..Sep-2024, which is not any single calendar
    year, so subtracting one from a year column would compare the wrong twelve
    months.
    """
    period = Period.coerce(year).whole()
    cy_filters, cy_params = _build_filters(year=period, regions=regions,
                                           subregions=subregions, channels=channels)
    py_filters, py_params = _build_filters(year=period.prior(), regions=regions,
                                           subregions=subregions, channels=channels)
    sql = f"""
    SELECT
        cy.month,
        cy.revenue              AS cy_revenue,
        cy.target               AS cy_target,
        py.revenue              AS py_revenue,
        CASE WHEN COALESCE(py.revenue,0)>0
             THEN ROUND((cy.revenue-py.revenue)/py.revenue*100,2) ELSE NULL END AS yoy_pct
    FROM (
        SELECT month, SUM(revenue) AS revenue, SUM(target) AS target
        FROM v_monthly_kpi {cy_filters} GROUP BY month
    ) cy
    LEFT JOIN (
        SELECT month, SUM(revenue) AS revenue
        FROM v_monthly_kpi {py_filters} GROUP BY month
    ) py ON cy.month = py.month
    ORDER BY {period.order_expr('cy.month')}
    """
    return query(sql, tuple(cy_params + py_params))


def get_top_regions(year: Period | int, month: int | None = None,
                    channels: list[str] | None = None,
                    limit: int = 10,
                    regions: list[str] | None = None,
                    subregions: list[str] | None = None) -> pd.DataFrame:
    """Top regions by revenue. active_clients from v_sales_base to avoid double-count.
    Now propagates the channel filter (§2.4).
    """
    kpi_filters, kpi_params = _build_filters(year=year, month=month, channels=channels,
                                             regions=regions, subregions=subregions,
                                             prefix="WHERE")
    # FIX: the clients CTE was built WITHOUT `channels`, so active_clients
    # ignored the channel filter while revenue/target respected it — the
    # docstring (§2.4) and get_regional_summary / get_category_performance
    # all propagate channels to both CTEs.
    base_filters, base_params = _build_filters(year=year, month=month, channels=channels,
                                                regions=regions, subregions=subregions,
                                                prefix="WHERE",
                                                year_col="sale_year", month_col="sale_month")
    sql = f"""
    WITH kpi AS (
        SELECT region,
               SUM(revenue) AS revenue,
               SUM(target)  AS target,
               CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct
        FROM v_monthly_kpi
        {kpi_filters}
        GROUP BY region
    ),
    clients AS (
        SELECT region, COUNT(DISTINCT clientsd_id) AS active_clients
        FROM v_sales_base
        {base_filters}
        GROUP BY region
    )
    SELECT k.region, k.revenue, k.target, k.achievement_pct,
           COALESCE(c.active_clients, 0) AS active_clients
    FROM kpi k
    LEFT JOIN clients c USING (region)
    ORDER BY revenue DESC
    LIMIT ?
    """
    return query(sql, tuple(kpi_params + base_params + [limit]))


# =============================================================================
# REGIONAL PERFORMANCE
# =============================================================================

def get_regional_summary(year: Period | int, month: int | None = None,
                          channels: list[str] | None = None,
                          regions: list[str] | None = None,
                          subregions: list[str] | None = None) -> pd.DataFrame:
    """Revenue/target by region × subregion.
    active_clients from v_sales_base to avoid cross-salesperson double-count.
    Clients CTE now uses _build_filters for consistency (§2.3).

    This is the builder behind the Regional Performance page, and the one where
    an ignored subregion selection was most visible: the page groups BY
    subregion, so an admin saw every subregion listed while the sidebar claimed
    three were selected.
    """
    kpi_filters, kpi_params = _build_filters(year=year, month=month,
                                              channels=channels, regions=regions,
                                              subregions=subregions, prefix="WHERE")
    base_filters, base_params = _build_filters(year=year, month=month,
                                                channels=channels, regions=regions,
                                                subregions=subregions, prefix="WHERE",
                                                year_col="sale_year", month_col="sale_month")

    sql = f"""
    WITH kpi AS (
        SELECT
            region, subregion,
            SUM(revenue)    AS revenue,
            SUM(target)     AS target,
            SUM(units_sold) AS units_sold,
            SUM(weight_kg)  AS weight_kg,
            CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct
        FROM v_monthly_kpi
        {kpi_filters}
        GROUP BY region, subregion
    ),
    clients AS (
        SELECT region, subregion, COUNT(DISTINCT clientsd_id) AS active_clients
        FROM v_sales_base
        {base_filters}
        GROUP BY region, subregion
    )
    SELECT k.region, k.subregion, k.revenue, k.target, k.units_sold, k.weight_kg,
           k.achievement_pct,
           COALESCE(c.active_clients, 0) AS active_clients
    FROM kpi k
    LEFT JOIN clients c USING (region, subregion)
    ORDER BY k.region, k.revenue DESC
    """
    return query(sql, tuple(kpi_params + base_params))


def get_region_monthly_trend(year: Period | int, region: str | list[str],
                             subregions: list[str] | None = None) -> pd.DataFrame:
    period = Period.coerce(year).whole()
    filters, params = _build_filters(year=period, regions=region,
                                     subregions=subregions, prefix="WHERE")
    sql = f"""
    SELECT month,
           SUM(revenue) AS revenue,
           SUM(target)  AS target
    FROM v_monthly_kpi
    {filters}
    GROUP BY month ORDER BY {period.order_expr('month')}
    """
    return query(sql, tuple(params))


def get_region_category_breakdown(year: Period | int, month: int | None = None,
                                   region: str | list[str] | None = None,
                                   channels: list[str] | None = None,
                                   subregions: list[str] | None = None) -> pd.DataFrame:
    """Category breakdown by region. Now accepts channels filter (§3.6).

    `region` goes through _build_filters instead of a hand-appended
    `AND region=?`, so a multi-select is honoured rather than only its first
    element, and the parameter order can no longer drift from the clause order.
    """
    filters, params = _build_filters(year=year, month=month, channels=channels,
                                     regions=region, subregions=subregions,
                                     prefix="WHERE")
    sql = f"""
    SELECT region, product_category,
           SUM(revenue) AS revenue, SUM(target) AS target, SUM(units_sold) AS units_sold,
           CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct
    FROM v_monthly_kpi
    {filters}
    GROUP BY region, product_category
    ORDER BY region, revenue DESC
    """
    return query(sql, tuple(params))


# =============================================================================
# SALESFORCE PERFORMANCE
# =============================================================================

def get_salesperson_ranking(year: Period | int, month: int | None = None,
                             regions: list[str] | None = None,
                             supervisor: str | None = None,
                             channel: str | None = None,
                             subregions: list[str] | None = None) -> pd.DataFrame:
    """Salesperson ranking. regions accepts a list for multi-region filtering.
    active_clients from v_sales_base to avoid cross-category double-count.

    The supervisor predicate stays hand-built: `supervisor_name` is NOT a
    sidebar dimension, it is a drill-down chosen on the page, and it exists on
    v_monthly_kpi but not on every view _dims() serves.
    """
    # --- KPI CTE filters (v_monthly_kpi columns) ---
    kpi_where, kpi_params = _build_filters(
        year=year, month=month, regions=regions, subregions=subregions,
        channels=channel, prefix="WHERE",
    )
    if supervisor:
        kpi_where += " AND supervisor_name=?"
        kpi_params.append(supervisor)

    # --- Clients CTE filters (v_sales_base columns) ---
    # No supervisor predicate here, deliberately and as before: active_clients
    # is counted per salesperson_id and joined back, so the supervisor
    # restriction on the kpi CTE already governs which rows survive the join.
    base_where, base_params = _build_filters(
        year=year, month=month, regions=regions, subregions=subregions,
        channels=channel, prefix="WHERE",
        year_col="sale_year", month_col="sale_month",
    )

    sql = f"""
    WITH kpi AS (
        SELECT
            salesperson_id, salesperson_name, region, subregion,
            supervisor_name, sales_channel,
            SUM(revenue)    AS revenue,
            SUM(target)     AS target,
            SUM(units_sold) AS units_sold,
            CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct
        FROM v_monthly_kpi
        {kpi_where}
        GROUP BY salesperson_id, salesperson_name, region, subregion,
                 supervisor_name, sales_channel
    ),
    clients AS (
        SELECT salesperson_id, COUNT(DISTINCT clientsd_id) AS active_clients
        FROM v_sales_base
        {base_where}
        GROUP BY salesperson_id
    )
    SELECT k.*, COALESCE(c.active_clients, 0) AS active_clients
    FROM kpi k
    LEFT JOIN clients c USING (salesperson_id)
    ORDER BY revenue DESC
    """
    return query(sql, tuple(kpi_params + base_params))


def get_supervisor_summary(year: Period | int, month: int | None = None,
                            region: str | list[str] | None = None,
                            channels: list[str] | None = None,
                            subregions: list[str] | None = None) -> pd.DataFrame:
    """Supervisor summary. Now propagates channels filter (§2.4)."""
    filters, params = _build_filters(year=year, month=month, channels=channels,
                                     regions=region, subregions=subregions,
                                     prefix="WHERE")
    sql = f"""
    SELECT supervisor_name, region,
           SUM(revenue) AS revenue, SUM(target) AS target,
           COUNT(DISTINCT salesperson_id) AS team_size,
           CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct
    FROM v_monthly_kpi
    {filters}
    GROUP BY supervisor_name, region
    ORDER BY revenue DESC
    """
    return query(sql, tuple(params))


def get_salesperson_monthly_trend(year: Period | int, salesperson_id: str) -> pd.DataFrame:
    period = Period.coerce(year).whole()
    period_sql, params = period.clause("year", "month")
    sql = f"""
    SELECT month, SUM(revenue) AS revenue, SUM(target) AS target, SUM(units_sold) AS units_sold
    FROM v_monthly_kpi
    WHERE {period_sql} AND salesperson_id=?
    GROUP BY month ORDER BY {period.order_expr('month')}
    """
    return query(sql, tuple(params + [salesperson_id]))


# =============================================================================
# PRODUCT PERFORMANCE
# =============================================================================

def get_category_performance(year: Period | int, month: int | None = None,
                              region: str | list[str] | None = None,
                              channels: list[str] | None = None,
                              subregions: list[str] | None = None) -> pd.DataFrame:
    """Category KPIs. UPPER() removed — category filter in page 4 relies on
    exact case-match; normalise at staging layer if needed, not here.
    active_clients from v_sales_base to avoid cross-salesperson double-count.
    Clients CTE now uses _build_filters for param consistency (§2.5).
    """
    kpi_filters, kpi_params = _build_filters(year=year, month=month, channels=channels,
                                             regions=region, subregions=subregions,
                                             prefix="WHERE")
    base_filters, base_params = _build_filters(year=year, month=month, channels=channels,
                                                regions=region, subregions=subregions,
                                                prefix="WHERE",
                                                year_col="sale_year", month_col="sale_month")

    sql = f"""
    WITH kpi AS (
        SELECT product_category,
               SUM(revenue) AS revenue,
               SUM(target)  AS target,
               SUM(units_sold) AS units_sold,
               SUM(weight_kg)  AS weight_kg,
               CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct
        FROM v_monthly_kpi
        {kpi_filters}
        GROUP BY product_category
    ),
    clients AS (
        SELECT product_category, COUNT(DISTINCT clientsd_id) AS active_clients
        FROM v_sales_base
        {base_filters}
        GROUP BY product_category
    )
    SELECT k.product_category, k.revenue, k.target, k.units_sold, k.weight_kg,
           k.achievement_pct,
           COALESCE(c.active_clients, 0) AS active_clients
    FROM kpi k
    LEFT JOIN clients c USING (product_category)
    ORDER BY revenue DESC
    """
    return query(sql, tuple(kpi_params + base_params))


def get_product_ranking(year: Period | int, month: int | None = None,
                         category: str | list[str] | None = None,
                         region: str | list[str] | None = None,
                         limit: int = 20,
                         subregions: list[str] | None = None,
                         channels: list[str] | None = None) -> pd.DataFrame:
    where, params = _build_filters(
        year=year, month=month, regions=region, subregions=subregions,
        channels=channels, categories=category, prefix="WHERE",
        year_col="sale_year", month_col="sale_month",
    )
    sql = f"""
    SELECT product_category, product_subcategory, product_name, sku,
           is_innovation_product,
           SUM(total_amount)           AS revenue,
           SUM(quantity)               AS units_sold,
           SUM(total_weight_kg)        AS weight_kg,
           AVG(price_variance_pct)     AS avg_price_variance_pct,
           COUNT(DISTINCT clientsd_id) AS active_clients
    FROM v_sales_base
    {where}
    GROUP BY product_category, product_subcategory, product_name, sku, is_innovation_product
    ORDER BY revenue DESC
    LIMIT ?
    """
    return query(sql, tuple(params + [limit]))


def get_innovation_performance(year: Period | int, month: int | None = None,
                               region: str | list[str] | None = None,
                               subregions: list[str] | None = None,
                               channels: list[str] | None = None) -> pd.DataFrame:
    where, params = _build_filters(
        year=year, month=month, regions=region, subregions=subregions,
        channels=channels, prefix="WHERE",
        year_col="sale_year", month_col="sale_month",
    )
    sql = f"""
    SELECT
        is_innovation_product,
        product_category,
        SUM(total_amount)           AS revenue,
        SUM(quantity)               AS units_sold,
        COUNT(DISTINCT clientsd_id) AS active_clients,
        COUNT(DISTINCT sku)         AS distinct_skus
    FROM v_sales_base
    {where}
    GROUP BY is_innovation_product, product_category
    ORDER BY is_innovation_product DESC, revenue DESC
    """
    return query(sql, tuple(params))


def get_innovation_trend(year: Period | int,
                         region: str | list[str] | None = None,
                         subregions: list[str] | None = None) -> pd.DataFrame:
    period = Period.coerce(year).whole()
    where, params = _build_filters(
        year=period, regions=region, subregions=subregions, prefix="WHERE",
        year_col="sale_year", month_col="sale_month",
    )
    sql = f"""
    SELECT sale_month AS month, is_innovation_product,
           SUM(total_amount) AS revenue, SUM(quantity) AS units_sold
    FROM v_sales_base
    {where}
    GROUP BY sale_month, is_innovation_product
    ORDER BY {period.order_expr('sale_month')}, is_innovation_product
    """
    return query(sql, tuple(params))


def get_category_monthly_trend(year: Period | int,
                               region: str | list[str] | None = None,
                               subregions: list[str] | None = None,
                               categories: list[str] | None = None) -> pd.DataFrame:
    """Monthly revenue by product_category — was an inline query in page 4 & 5."""
    period = Period.coerce(year).whole()
    where, params = _build_filters(
        year=period, regions=region, subregions=subregions, categories=categories,
        prefix="WHERE", year_col="sale_year", month_col="sale_month",
    )
    sql = f"""
    SELECT sale_month AS month, product_category,
           SUM(total_amount) AS revenue
    FROM v_sales_base
    {where}
    GROUP BY sale_month, product_category
    ORDER BY {period.order_expr('sale_month')}, product_category
    """
    return query(sql, tuple(params))


# =============================================================================
# TIME INTELLIGENCE
# =============================================================================

def get_weekly_performance(year: Period | int, month: int | None = None,
                            region: str | list[str] | None = None,
                            subregions: list[str] | None = None) -> pd.DataFrame:
    where, params = _build_filters(
        year=year, month=month, regions=region, subregions=subregions,
        prefix="WHERE", year_col="sale_year", month_col="sale_month",
    )
    sql = f"""
    SELECT week_of_month, week_of_year,
           SUM(revenue) AS revenue, SUM(units_sold) AS units_sold,
           SUM(active_clients) AS active_clients
    FROM v_weekly_kpi
    {where}
    GROUP BY week_of_month, week_of_year
    ORDER BY week_of_month
    """
    return query(sql, tuple(params))


def get_week_over_week(year: Period | int, month: int | None = None,
                       region: str | list[str] | None = None,
                       subregions: list[str] | None = None) -> pd.DataFrame:
    """WoW change within the selected month.

    Now takes the scope filters. Without them the four KPI cards on the
    Week-over-Week tab were national totals while the chart beside them was
    filtered — two numbers on one screen answering different questions.
    """
    where, params = _build_filters(
        year=year, month=month, regions=region, subregions=subregions,
        prefix="WHERE", year_col="sale_year", month_col="sale_month",
    )
    sql = f"""
    WITH wkly AS (
        SELECT week_of_month, SUM(revenue) AS revenue, SUM(units_sold) AS units_sold
        FROM v_weekly_kpi
        {where}
        GROUP BY week_of_month
    )
    SELECT
        w.week_of_month,
        w.revenue,
        w.units_sold,
        LAG(w.revenue) OVER (ORDER BY w.week_of_month) AS prev_week_revenue,
        CASE WHEN LAG(w.revenue) OVER (ORDER BY w.week_of_month) > 0
             THEN ROUND((w.revenue - LAG(w.revenue) OVER (ORDER BY w.week_of_month))
                  / LAG(w.revenue) OVER (ORDER BY w.week_of_month) * 100, 2)
             ELSE NULL END AS wow_pct
    FROM wkly w
    ORDER BY week_of_month
    """
    return query(sql, tuple(params))


def get_region_weekly(year: Period | int, month: int | None = None,
                      region: str | list[str] | None = None,
                      subregions: list[str] | None = None) -> pd.DataFrame:
    """Weekly revenue by region — was an inline query in page 5."""
    where, params = _build_filters(
        year=year, month=month, regions=region, subregions=subregions,
        prefix="WHERE", year_col="sale_year", month_col="sale_month",
    )
    sql = f"""
    SELECT week_of_month, region, SUM(revenue) AS revenue
    FROM v_weekly_kpi
    {where}
    GROUP BY week_of_month, region
    ORDER BY week_of_month, region
    """
    return query(sql, tuple(params))


def get_same_month_last_year(year: Period | int, month: int | None = None,
                              region: str | list[str] | None = None,
                              subregions: list[str] | None = None) -> pd.DataFrame:
    period = Period.coerce(year, month)
    cy_where, cy_params = _build_filters(year=period, regions=region,
                                         subregions=subregions, prefix="WHERE")
    py_where, py_params = _build_filters(year=period.prior(), regions=region,
                                         subregions=subregions, prefix="WHERE")
    sql = f"""
    SELECT 'Current Year' AS period, SUM(revenue) AS revenue, SUM(target) AS target,
           SUM(units_sold) AS units_sold
    FROM v_monthly_kpi {cy_where}
    UNION ALL
    SELECT 'Prior Year', SUM(revenue), SUM(target), SUM(units_sold)
    FROM v_monthly_kpi {py_where}
    """
    return query(sql, tuple(cy_params + py_params))


def get_region_yoy(year: Period | int, month: int | None = None,
                   subregions: list[str] | None = None) -> pd.DataFrame:
    """Regional CY vs PY for a specific month — was an inline query in page 5.

    The two CASE branches and the WHERE are built from the SAME period
    predicates, so a fiscal month lands in the right calendar year on both
    sides. The previous form leaned on parameter position alone
    (`year=?` twice, bound to year and year-1), which was correct but one
    argument-reorder away from silently comparing a year with itself.
    """
    period = Period.coerce(year, month)
    cy_sql, cy_params = period.clause("year", "month")
    py_sql, py_params = period.prior().clause("year", "month")
    dim_clauses, dim_params = _dims(subregions=subregions)
    dim_sql = (" AND " + " AND ".join(dim_clauses)) if dim_clauses else ""

    sql = f"""
    SELECT region,
           SUM(CASE WHEN {cy_sql} THEN revenue ELSE 0 END) AS cy_revenue,
           SUM(CASE WHEN {py_sql} THEN revenue ELSE 0 END) AS py_revenue
    FROM v_monthly_kpi
    WHERE ({cy_sql} OR {py_sql}) {dim_sql}
    GROUP BY region
    ORDER BY cy_revenue DESC
    """
    return query(sql, tuple(cy_params + py_params
                            + cy_params + py_params + dim_params))


def get_seasonality_heatmap(year: Period | int,
                            region: str | list[str] | None = None,
                            subregions: list[str] | None = None) -> pd.DataFrame:
    """Month × Weekday revenue heatmap data."""
    period = Period.coerce(year).whole()
    where, params = _build_filters(year=period, regions=region, subregions=subregions,
                                   prefix="WHERE",
                                   year_col="sale_year", month_col="sale_month")
    sql = f"""
    SELECT sale_month AS month, day_of_week,
           SUM(total_amount) AS revenue
    FROM v_sales_base
    {where}
    GROUP BY sale_month, day_of_week
    ORDER BY {period.order_expr('sale_month')}, day_of_week
    """
    return query(sql, tuple(params))


def get_category_month_heatmap(year: Period | int,
                               region: str | list[str] | None = None,
                               subregions: list[str] | None = None) -> pd.DataFrame:
    """Category × Month revenue matrix — was an inline query in page 5."""
    period = Period.coerce(year).whole()
    where, params = _build_filters(year=period, regions=region, subregions=subregions,
                                   prefix="WHERE",
                                   year_col="sale_year", month_col="sale_month")
    sql = f"""
    SELECT sale_month AS month, product_category,
           SUM(total_amount) AS revenue
    FROM v_sales_base
    {where}
    GROUP BY sale_month, product_category
    ORDER BY {period.order_expr('sale_month')}
    """
    return query(sql, tuple(params))


def _view_exists(view_name: str) -> bool:
    """True when a view/table exists in the bi schema.
    """
    return object_exists(view_name, "bi")


def get_quarterly_summary(year: Period | int,
                          region: str | list[str] | None = None,
                          subregions: list[str] | None = None) -> pd.DataFrame:
    """Quarterly summary with graceful fallback to v_monthly_kpi (§3.8).

    UNDER A FISCAL BASIS THE FALLBACK IS TAKEN DELIBERATELY, even when
    v_quarterly_kpi exists. That view exposes `year` and `quarter` and no
    month, so there is no way to ask it for Oct-2025..Sep-2026 — and no way to
    verify from here whether its `year` is the calendar year of the quarter or
    the fiscal year it belongs to. Reconstructing quarters from v_monthly_kpi
    costs one aggregation and is unambiguous. If v_quarterly_kpi ever grows a
    fiscal_year column, prefer it and delete this branch.
    """
    period = Period.coerce(year).whole()
    use_quarterly_view = (not period.fiscal) and _view_exists("v_quarterly_kpi")

    if use_quarterly_view:
        where, params = _build_filters(year=period, regions=region,
                                       subregions=subregions, prefix="WHERE")
        sql = f"""
        SELECT quarter, region,
               SUM(revenue) AS revenue,
               SUM(target)  AS target,
               SUM(units_sold) AS units_sold,
               CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct
        FROM v_quarterly_kpi
        {where}
        GROUP BY quarter, region
        ORDER BY quarter, region
        """
    else:
        where, params = _build_filters(year=period, regions=region,
                                       subregions=subregions, prefix="WHERE")
        # FIX (bug): this fallback used calendar quarters (CEIL(month/3.0)),
        # i.e. Q1=Jan-Mar, which doesn't match the company's Oct-start
        # fiscal year used everywhere else (see filters.py QUARTER_MONTHS:
        # Q1=Oct-Dec, Q2=Jan-Mar, Q3=Apr-Jun, Q4=Jul-Sep) and presumably
        # baked into v_quarterly_kpi via dim_date's fiscal quarter mapping.
        # An October sale used to land in "Q4" here but "Q1" in the primary
        # path — silently wrong the moment v_quarterly_kpi went missing.
        # Fiscal-month formula: re-index months so Oct=1 .. Sep=12, then
        # group into quarters of 3 fiscal months.
        sql = f"""
        SELECT
            CEIL((((month - 10 + 12) % 12) + 1) / 3.0)::INT AS quarter,
            region,
            SUM(revenue) AS revenue,
            SUM(target)  AS target,
            SUM(units_sold) AS units_sold,
            CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct
        FROM v_monthly_kpi
        {where}
        GROUP BY CEIL((((month - 10 + 12) % 12) + 1) / 3.0)::INT, region
        ORDER BY quarter, region
        """
    return query(sql, tuple(params))

# =============================================================================
# SELL-IN / SELL-OUT  (KP -> SD)
#
# Was inline in reporting/pages/8_Sell_In_Sell_Out.py via reporting.utils.views,
# whose constants carry a "main." prefix from the serving.db era. Bare names
# here, resolved by the search path db.get_connection() sets — same contract as
# every other builder in this module.
# =============================================================================

SELLIN_KPI_VIEW = "v_sellin_sellout_kpi"
SELLIN_BASE_VIEW = "v_kp_sd_base"


def _period_columns(view: str) -> tuple[str, str] | None:
    """Which pair of columns carries (year, month) on this view?

    The KP-SD chain is inconsistent: v_sellin_sellout_kpi aggregates to
    year/month, while v_kp_sd_base is row-level and carries sale_year/
    sale_month. Rather than hardcode a guess per view -- the previous code
    carried a ⚠ admitting it WAS a guess -- ask the catalog once and cache it
    for the query TTL. Returns None when neither pair is present, which the
    caller renders as a clear message instead of a binder error.
    """
    cols = object_columns(view, "bi")
    if not cols:
        return None
    for pair in (("year", "month"), ("sale_year", "sale_month")):
        if pair[0] in cols and pair[1] in cols:
            return pair
    return None


def sellin_scope_support() -> set[str]:
    """Which scope columns v_sellin_sellout_kpi actually carries today.

    region/subregion/supervisor_name are marked [PATCH] on this view in
    rls.py -- they exist only once bi_views_rls_patch.sql has been applied and
    `sqlmesh plan` has rebuilt. The page uses this to say "the subregion filter
    does not apply here yet" rather than either crashing or, worse, appearing
    to filter while ignoring the selection.
    """
    return {c for c in ("region", "subregion", "supervisor_name", "clientsd_id")
            if c in object_columns(SELLIN_KPI_VIEW, "bi")}


def get_sellin_years(fiscal: bool = False) -> list[int]:
    """Years that have sell-in data, newest first. Fiscal years when asked."""
    cols = _period_columns(SELLIN_KPI_VIEW)
    if cols is None:
        return []
    year_col, month_col = cols
    df = query(
        f"SELECT DISTINCT {year_col} AS y, {month_col} AS m "
        f"FROM {SELLIN_KPI_VIEW} WHERE {year_col} IS NOT NULL"
    )
    if df.empty:
        return []
    if not fiscal:
        return sorted({int(v) for v in df["y"].dropna()}, reverse=True)
    from reporting.utils.period import fiscal_year_of
    return sorted({fiscal_year_of(int(r.y), int(r.m))
                   for r in df.dropna().itertuples()}, reverse=True)


def get_sellin_months(year: Period | int) -> list[int]:
    cols = _period_columns(SELLIN_KPI_VIEW)
    if cols is None:
        return []
    period = Period.coerce(year).whole()
    where, params = period.clause(*cols)
    df = query(
        f"SELECT DISTINCT {cols[1]} AS m FROM {SELLIN_KPI_VIEW} "
        f"WHERE {where} ORDER BY m",
        tuple(params),
    )
    return [int(v) for v in df["m"].dropna()] if not df.empty else []


def get_sellin_sellout(year: Period | int, month: int | None = None,
                       subregions: list[str] | None = None,
                       regions: list[str] | None = None) -> pd.DataFrame:
    """Sell-in vs sell-out by SD and product for the period.

    Bare view name. The page used to reach this view through
    reporting.utils.views, whose constants still carried a `main.` prefix from
    the serving.db era -- which is why the page failed with
    `Table with name v_sellin_sellout_kpi does not exist! Did you mean
    "bi__dev.v_sellin_sellout_kpi"?`. `main` is DuckDB's default schema in the
    in-memory database the app connects to, not the lake. Bare names resolve
    through the search path db.py sets, and are what rls.py matches on.
    """
    cols = _period_columns(SELLIN_KPI_VIEW)
    if cols is None:
        return pd.DataFrame()

    period = Period.coerce(year, month)
    where, params = period.clause(*cols)
    clauses = [where]

    supported = sellin_scope_support()
    for column, values in (("region", _as_list(regions)),
                           ("subregion", _as_list(subregions))):
        if values and column in supported:
            sql, ps = _in_clause(column, values)
            clauses.append(sql)
            params.extend(ps)

    sql = f"SELECT * FROM {SELLIN_KPI_VIEW} WHERE {' AND '.join(clauses)}"
    return query(sql, tuple(params))


def get_destocked_flag_mismatches(year: Period | int, month: int | None = None,
                                  subregions: list[str] | None = None) -> pd.DataFrame:
    """Sell-in rows whose source-asserted destockage disagrees with the SD flag.

    Period columns are resolved from the catalog rather than assumed -- see
    _period_columns. subregion is filtered only when v_kp_sd_base carries it.
    """
    cols = _period_columns(SELLIN_BASE_VIEW)
    if cols is None:
        return pd.DataFrame()

    base_cols = object_columns(SELLIN_BASE_VIEW, "bi")
    if "destocked_flag_mismatch" not in base_cols:
        return pd.DataFrame()

    period = Period.coerce(year, month)
    where, params = period.clause(*cols)
    clauses = [where, "destocked_flag_mismatch"]

    values = _as_list(subregions)
    if values and "subregion" in base_cols:
        sql, ps = _in_clause("subregion", values)
        clauses.append(sql)
        params.extend(ps)

    wanted = ["clientsd_id", "client_name", "sale_date", "sku",
              "source_asserted_destocked"]
    select_list = ", ".join(c for c in wanted if c in base_cols) or "*"

    sql = f"""
    SELECT {select_list}
    FROM {SELLIN_BASE_VIEW}
    WHERE {' AND '.join(clauses)}
    """
    return query(sql, tuple(params))

# =============================================================================
# HELPERS
# =============================================================================

def _as_list(value) -> list | None:
    """Normalise a filter argument to a non-empty list, or None.

    Several builders historically took a SINGLE `region: str`, and the pages
    passed `regions[0] if len(regions) == 1 else None` -- so selecting two
    regions silently filtered by neither. Accepting both shapes here fixes that
    without changing a signature the other pages still call.
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    out = [v for v in value if v is not None and v != ""]
    return out or None


def _in_clause(column: str, values: list) -> tuple[str, list]:
    holes = ",".join(["?"] * len(values))
    return f"{column} IN ({holes})", list(values)


def _dims(regions=None, subregions=None, channels=None,
          categories=None) -> tuple[list[str], list]:
    """Dimension predicates shared by every builder, in a FIXED order.

    Order matters: the caller splices these fragments into SQL and concatenates
    the parameter lists, so clause order and parameter order must be produced
    by the same function or they will drift apart the first time someone adds a
    dimension.
    """
    clauses: list[str] = []
    params: list = []
    for column, values in (
        ("region", _as_list(regions)),
        ("subregion", _as_list(subregions)),
        ("sales_channel", _as_list(channels)),
        ("product_category", _as_list(categories)),
    ):
        if values:
            sql, ps = _in_clause(column, values)
            clauses.append(sql)
            params.extend(ps)
    return clauses, params


def _build_filters(year: Period | int,
                   month: int | None = None,
                   regions: list[str] | str | None = None,
                   subregions: list[str] | str | None = None,
                   channels: list[str] | str | None = None,
                   categories: list[str] | str | None = None,
                   prefix: str = "WHERE",
                   year_col: str = "year",
                   month_col: str = "month") -> tuple[str, list]:
    """(sql_fragment, params) for a period plus the dimension selection.

    `year` may be a Period or a calendar year; `month` narrows either.
    """
    period = Period.coerce(year, month)
    period_sql, params = period.clause(year_col, month_col)
    clauses = [period_sql]

    dim_clauses, dim_params = _dims(regions, subregions, channels, categories)
    clauses.extend(dim_clauses)
    params.extend(dim_params)

    joined = " AND ".join(clauses)
    return (f"{prefix} {joined}" if prefix else joined), params