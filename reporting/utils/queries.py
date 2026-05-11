"""
reporting/utils/queries.py
All SQL query builders for the reporting pages.
Returns pd.DataFrames via db.query().

Fixes applied:
  - _build_filters: year is now a bound parameter (was f-string interpolated)
  - get_executive_kpis: accepts regions / channels scope filters
  - get_ytd_vs_prior_year: uses explicit year-1 param (was ?-1)
  - get_regional_summary / get_top_regions / get_category_performance /
    get_salesperson_ranking: active_clients computed via COUNT(DISTINCT clientsd_id)
    from v_sales_base to prevent double-counting across salespersons/categories
  - get_category_performance: UPPER() removed (was breaking category filter in page 4)
  - get_salesperson_ranking: region param is now a list (multi-region support)
  - Inline page SQL extracted into named functions:
      get_category_monthly_trend, get_region_weekly, get_region_yoy,
      get_category_month_heatmap
"""
from __future__ import annotations
import pandas as pd
from reporting.utils.db import query


# =============================================================================
# EXECUTIVE OVERVIEW
# =============================================================================

def get_executive_kpis(year: int, month: int | None = None,
                        regions: list[str] | None = None,
                        channels: list[str] | None = None) -> pd.DataFrame:
    """Top-level KPIs: revenue, target, achievement, units, weight, clients, transactions.
    active_clients drawn from v_sales_base to avoid cross-salesperson double-counting.
    """
    kpi_filters, kpi_params = _build_filters(year=year, month=month,
                                              regions=regions, channels=channels,
                                              prefix="WHERE")
    base_filters, base_params = _build_filters(year=year, month=month,
                                                regions=regions, channels=channels,
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


def get_monthly_trend(year: int, regions: list[str] | None = None,
                      channels: list[str] | None = None) -> pd.DataFrame:
    """Monthly revenue vs target trend for the year."""
    filters, params = _build_filters(year=year, regions=regions, channels=channels,
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
    ORDER BY month
    """
    return query(sql, tuple(params))


def get_ytd_vs_prior_year(year: int) -> pd.DataFrame:
    """YTD revenue current year vs prior year, month by month."""
    sql = """
    SELECT
        cy.month,
        cy.revenue              AS cy_revenue,
        cy.target               AS cy_target,
        py.revenue              AS py_revenue,
        CASE WHEN COALESCE(py.revenue,0)>0
             THEN ROUND((cy.revenue-py.revenue)/py.revenue*100,2) ELSE NULL END AS yoy_pct
    FROM (
        SELECT month, SUM(revenue) AS revenue, SUM(target) AS target
        FROM v_monthly_kpi WHERE year=? GROUP BY month
    ) cy
    LEFT JOIN (
        SELECT month, SUM(revenue) AS revenue
        FROM v_monthly_kpi WHERE year=? GROUP BY month
    ) py ON cy.month = py.month
    ORDER BY cy.month
    """
    return query(sql, (year, year - 1))


def get_top_regions(year: int, month: int | None = None, limit: int = 10) -> pd.DataFrame:
    """Top regions by revenue. active_clients from v_sales_base to avoid double-count."""
    kpi_filters, kpi_params = _build_filters(year=year, month=month, prefix="WHERE")
    base_filters, base_params = _build_filters(year=year, month=month, prefix="WHERE",
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
    LIMIT {limit}
    """
    return query(sql, tuple(kpi_params + base_params))


# =============================================================================
# REGIONAL PERFORMANCE
# =============================================================================

def get_regional_summary(year: int, month: int | None = None,
                          channels: list[str] | None = None) -> pd.DataFrame:
    """Revenue/target by region × subregion.
    active_clients from v_sales_base to avoid cross-salesperson double-count.
    """
    ch_clause = f"AND sales_channel IN ({','.join(['?']*len(channels))})" if channels else ""
    kpi_filters, kpi_params = _build_filters(year=year, month=month,
                                              channels=channels, prefix="WHERE")
    base_m     = "AND sale_month=?" if month else ""
    base_ch    = ch_clause
    base_params = ([year] + ([month] if month else []) + (channels or []))

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
        WHERE sale_year=? {base_m} {base_ch}
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


def get_region_monthly_trend(year: int, region: str) -> pd.DataFrame:
    sql = """
    SELECT month,
           SUM(revenue) AS revenue,
           SUM(target)  AS target
    FROM v_monthly_kpi
    WHERE year=? AND region=?
    GROUP BY month ORDER BY month
    """
    return query(sql, (year, region))


def get_region_category_breakdown(year: int, month: int | None,
                                   region: str | None = None) -> pd.DataFrame:
    filters, params = _build_filters(year=year, month=month, prefix="WHERE")
    r_clause = ""
    if region:
        r_clause = "AND region=?"
        params = list(params) + [region]
    sql = f"""
    SELECT region, product_category,
           SUM(revenue) AS revenue, SUM(target) AS target, SUM(units_sold) AS units_sold,
           CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct
    FROM v_monthly_kpi
    {filters} {r_clause}
    GROUP BY region, product_category
    ORDER BY region, revenue DESC
    """
    return query(sql, tuple(params))


# =============================================================================
# SALESFORCE PERFORMANCE
# =============================================================================

def get_salesperson_ranking(year: int, month: int | None = None,
                             regions: list[str] | None = None,
                             supervisor: str | None = None,
                             channel: str | None = None) -> pd.DataFrame:
    """Salesperson ranking. regions accepts a list for multi-region filtering.
    active_clients from v_sales_base to avoid cross-category double-count.
    """
    # --- KPI CTE filters (v_monthly_kpi columns) ---
    kpi_clauses = ["year=?"]
    kpi_params: list = [year]
    if month:
        kpi_clauses.append("month=?"); kpi_params.append(month)
    if regions:
        kpi_clauses.append(f"region IN ({','.join(['?']*len(regions))})"); kpi_params.extend(regions)
    if supervisor:
        kpi_clauses.append("supervisor_name=?"); kpi_params.append(supervisor)
    if channel:
        kpi_clauses.append("sales_channel=?"); kpi_params.append(channel)
    kpi_where = "WHERE " + " AND ".join(kpi_clauses)

    # --- Clients CTE filters (v_sales_base columns) ---
    base_clauses = ["sale_year=?"]
    base_params: list = [year]
    if month:
        base_clauses.append("sale_month=?"); base_params.append(month)
    if regions:
        base_clauses.append(f"region IN ({','.join(['?']*len(regions))})"); base_params.extend(regions)
    if channel:
        base_clauses.append("sales_channel=?"); base_params.append(channel)
    base_where = "WHERE " + " AND ".join(base_clauses)

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


def get_supervisor_summary(year: int, month: int | None = None,
                            region: str | None = None) -> pd.DataFrame:
    filters, params = _build_filters(year=year, month=month, prefix="WHERE")
    r_clause = ""
    if region:
        r_clause = "AND region=?"
        params = list(params) + [region]
    sql = f"""
    SELECT supervisor_name, region,
           SUM(revenue) AS revenue, SUM(target) AS target,
           COUNT(DISTINCT salesperson_id) AS team_size,
           CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct
    FROM v_monthly_kpi
    {filters} {r_clause}
    GROUP BY supervisor_name, region
    ORDER BY revenue DESC
    """
    return query(sql, tuple(params))


def get_salesperson_monthly_trend(year: int, salesperson_id: str) -> pd.DataFrame:
    sql = """
    SELECT month, SUM(revenue) AS revenue, SUM(target) AS target, SUM(units_sold) AS units_sold
    FROM v_monthly_kpi
    WHERE year=? AND salesperson_id=?
    GROUP BY month ORDER BY month
    """
    return query(sql, (year, salesperson_id))


# =============================================================================
# PRODUCT PERFORMANCE
# =============================================================================

def get_category_performance(year: int, month: int | None = None,
                              region: str | None = None) -> pd.DataFrame:
    """Category KPIs. UPPER() removed — category filter in page 4 relies on
    exact case-match; normalise at staging layer if needed, not here.
    active_clients from v_sales_base to avoid cross-salesperson double-count.
    """
    kpi_filters, kpi_params = _build_filters(year=year, month=month, prefix="WHERE")
    base_m = "AND sale_month=?" if month else ""
    base_params = [year] + ([month] if month else [])
    r_clause = ""
    if region:
        r_clause = "AND region=?"
        kpi_params = list(kpi_params) + [region]
        base_params = base_params + [region]

    sql = f"""
    WITH kpi AS (
        SELECT product_category,
               SUM(revenue) AS revenue,
               SUM(target)  AS target,
               SUM(units_sold) AS units_sold,
               SUM(weight_kg)  AS weight_kg,
               CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct
        FROM v_monthly_kpi
        {kpi_filters} {r_clause}
        GROUP BY product_category
    ),
    clients AS (
        SELECT product_category, COUNT(DISTINCT clientsd_id) AS active_clients
        FROM v_sales_base
        WHERE sale_year=? {base_m} {r_clause}
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


def get_product_ranking(year: int, month: int | None = None,
                         category: str | None = None,
                         region: str | None = None,
                         limit: int = 20) -> pd.DataFrame:
    filters = ["sale_year=?"]
    params: list = [year]
    if month:
        filters.append("sale_month=?"); params.append(month)
    if category:
        filters.append("product_category=?"); params.append(category)
    if region:
        filters.append("region=?"); params.append(region)
    where = "WHERE " + " AND ".join(filters)
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
    LIMIT {limit}
    """
    return query(sql, tuple(params))


def get_innovation_performance(year: int, month: int | None = None) -> pd.DataFrame:
    filters = ["sale_year=?"]
    params: list = [year]
    if month:
        filters.append("sale_month=?"); params.append(month)
    where = "WHERE " + " AND ".join(filters)
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


def get_innovation_trend(year: int) -> pd.DataFrame:
    sql = """
    SELECT sale_month AS month, is_innovation_product,
           SUM(total_amount) AS revenue, SUM(quantity) AS units_sold
    FROM v_sales_base
    WHERE sale_year=?
    GROUP BY sale_month, is_innovation_product
    ORDER BY sale_month, is_innovation_product
    """
    return query(sql, (year,))


def get_category_monthly_trend(year: int) -> pd.DataFrame:
    """Monthly revenue by product_category — was an inline query in page 4 & 5."""
    sql = """
    SELECT sale_month AS month, product_category,
           SUM(total_amount) AS revenue
    FROM v_sales_base
    WHERE sale_year=?
    GROUP BY sale_month, product_category
    ORDER BY sale_month, product_category
    """
    return query(sql, (year,))


# =============================================================================
# TIME INTELLIGENCE
# =============================================================================

def get_weekly_performance(year: int, month: int,
                            region: str | None = None) -> pd.DataFrame:
    r_filter = "AND region=?" if region else ""
    params = [year, month] + ([region] if region else [])
    sql = f"""
    SELECT week_of_month, week_of_year,
           SUM(revenue) AS revenue, SUM(units_sold) AS units_sold,
           SUM(active_clients) AS active_clients
    FROM v_weekly_kpi
    WHERE sale_year=? AND sale_month=? {r_filter}
    GROUP BY week_of_month, week_of_year
    ORDER BY week_of_month
    """
    return query(sql, tuple(params))


def get_week_over_week(year: int, month: int) -> pd.DataFrame:
    """WoW change within the selected month."""
    sql = """
    WITH wkly AS (
        SELECT week_of_month, SUM(revenue) AS revenue, SUM(units_sold) AS units_sold
        FROM v_weekly_kpi
        WHERE sale_year=? AND sale_month=?
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
    return query(sql, (year, month))


def get_region_weekly(year: int, month: int) -> pd.DataFrame:
    """Weekly revenue by region — was an inline query in page 5."""
    sql = """
    SELECT week_of_month, region, SUM(revenue) AS revenue
    FROM v_weekly_kpi
    WHERE sale_year=? AND sale_month=?
    GROUP BY week_of_month, region
    ORDER BY week_of_month, region
    """
    return query(sql, (year, month))


def get_same_month_last_year(year: int, month: int,
                              region: str | None = None) -> pd.DataFrame:
    r_filter = "AND region=?" if region else ""
    params_cy = [year, month] + ([region] if region else [])
    params_py = [year - 1, month] + ([region] if region else [])
    sql = f"""
    SELECT 'Current Year' AS period, SUM(revenue) AS revenue, SUM(target) AS target,
           SUM(units_sold) AS units_sold
    FROM v_monthly_kpi WHERE year=? AND month=? {r_filter}
    UNION ALL
    SELECT 'Prior Year', SUM(revenue), SUM(target), SUM(units_sold)
    FROM v_monthly_kpi WHERE year=? AND month=? {r_filter}
    """
    return query(sql, tuple(params_cy + params_py))


def get_region_yoy(year: int, month: int) -> pd.DataFrame:
    """Regional CY vs PY for a specific month — was an inline query in page 5."""
    sql = """
    SELECT region,
           SUM(CASE WHEN year=? THEN revenue ELSE 0 END) AS cy_revenue,
           SUM(CASE WHEN year=? THEN revenue ELSE 0 END) AS py_revenue
    FROM v_monthly_kpi
    WHERE year IN (?, ?) AND month=?
    GROUP BY region
    ORDER BY cy_revenue DESC
    """
    return query(sql, (year, year - 1, year, year - 1, month))


def get_seasonality_heatmap(year: int, region: str | None = None) -> pd.DataFrame:
    """Month × Weekday revenue heatmap data."""
    r_filter = "AND region=?" if region else ""
    params = [year] + ([region] if region else [])
    sql = f"""
    SELECT sale_month AS month, day_of_week,
           SUM(total_amount) AS revenue
    FROM v_sales_base
    WHERE sale_year=? {r_filter}
    GROUP BY sale_month, day_of_week
    ORDER BY sale_month, day_of_week
    """
    return query(sql, tuple(params))


def get_category_month_heatmap(year: int) -> pd.DataFrame:
    """Category × Month revenue matrix — was an inline query in page 5."""
    sql = """
    SELECT sale_month AS month, product_category,
           SUM(total_amount) AS revenue
    FROM v_sales_base
    WHERE sale_year=?
    GROUP BY sale_month, product_category
    ORDER BY sale_month
    """
    return query(sql, (year,))


def get_quarterly_summary(year: int, region: str | None = None) -> pd.DataFrame:
    r_filter = "AND region=?" if region else ""
    params = [year] + ([region] if region else [])
    sql = f"""
    SELECT quarter, region,
           SUM(revenue) AS revenue,
           SUM(target)  AS target,
           SUM(units_sold) AS units_sold,
           CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct
    FROM v_quarterly_kpi
    WHERE year=? {r_filter}
    GROUP BY quarter, region
    ORDER BY quarter, region
    """
    return query(sql, tuple(params))


# =============================================================================
# HELPERS
# =============================================================================

def _build_filters(year: int,
                   month: int | None = None,
                   regions: list[str] | None = None,
                   channels: list[str] | None = None,
                   prefix: str = "WHERE",
                   year_col: str = "year",
                   month_col: str = "month") -> tuple[str, list]:
    """
    Build a parameterised WHERE clause.

    year_col / month_col allow switching between v_monthly_kpi (year / month)
    and v_sales_base (sale_year / sale_month) column names.

    Returns (clause_string, params_list).  Year is always a bound parameter —
    never f-string-interpolated — so @st.cache_data keys are stable across years.
    """
    clauses = [f"{year_col}=?"]
    params: list = [year]
    if month:
        clauses.append(f"{month_col}=?"); params.append(month)
    if regions:
        placeholders = ",".join(["?"] * len(regions))
        clauses.append(f"region IN ({placeholders})")
        params.extend(regions)
    if channels:
        placeholders = ",".join(["?"] * len(channels))
        clauses.append(f"sales_channel IN ({placeholders})")
        params.extend(channels)
    return (f"{prefix} " + " AND ".join(clauses)), params
