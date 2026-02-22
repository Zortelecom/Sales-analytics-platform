"""
reporting/utils/queries.py
All SQL query builders for the reporting pages.
Returns pd.DataFrames via db.query().
"""
from __future__ import annotations
import pandas as pd
from reporting.utils.db import query


# =============================================================================
# EXECUTIVE OVERVIEW
# =============================================================================

def get_executive_kpis(year: int, month: int | None = None) -> pd.DataFrame:
    """Top-level KPIs: revenue, target, achievement, units, clients."""
    if month:
        sql = """
        SELECT
            SUM(revenue)        AS total_revenue,
            SUM(target)         AS total_target,
            CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct,
            SUM(units_sold)     AS total_units,
            SUM(weight_kg)      AS total_weight_kg,
            SUM(active_clients) AS total_clients,
            SUM(transaction_count) AS total_transactions
        FROM v_monthly_kpi
        WHERE year=? AND month=?
        """
        return query(sql, (year, month))
    else:
        sql = """
        SELECT
            SUM(revenue)        AS total_revenue,
            SUM(target)         AS total_target,
            CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct,
            SUM(units_sold)     AS total_units,
            SUM(weight_kg)      AS total_weight_kg,
            SUM(active_clients) AS total_clients,
            SUM(transaction_count) AS total_transactions
        FROM v_monthly_kpi
        WHERE year=?
        """
        return query(sql, (year,))


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
        FROM v_monthly_kpi WHERE year=?-1 GROUP BY month
    ) py ON cy.month = py.month
    ORDER BY cy.month
    """
    return query(sql, (year, year))


def get_top_regions(year: int, month: int | None = None, limit: int = 10) -> pd.DataFrame:
    m_filter = "AND month=?" if month else ""
    params = (year, month) if month else (year,)
    sql = f"""
    SELECT region, SUM(revenue) AS revenue, SUM(target) AS target,
           CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct
    FROM v_monthly_kpi
    WHERE year=? {m_filter}
    GROUP BY region
    ORDER BY revenue DESC
    LIMIT {limit}
    """
    return query(sql, params)


# =============================================================================
# REGIONAL PERFORMANCE
# =============================================================================

def get_regional_summary(year: int, month: int | None = None,
                          channels: list[str] | None = None) -> pd.DataFrame:
    m_filter = "AND month=?" if month else ""
    ch_filter = f"AND sales_channel IN ({','.join(['?']*len(channels))})" if channels else ""
    params = [year] + ([month] if month else []) + (channels or [])
    sql = f"""
    SELECT
        region,
        subregion,
        SUM(revenue)    AS revenue,
        SUM(target)     AS target,
        SUM(units_sold) AS units_sold,
        SUM(weight_kg)  AS weight_kg,
        SUM(active_clients) AS active_clients,
        CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct
    FROM v_monthly_kpi
    WHERE year=? {m_filter} {ch_filter}
    GROUP BY region, subregion
    ORDER BY region, revenue DESC
    """
    return query(sql, tuple(params))


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
    m_filter = "AND month=?" if month else ""
    r_filter = "AND region=?" if region else ""
    params = [year] + ([month] if month else []) + ([region] if region else [])
    sql = f"""
    SELECT region, product_category,
           SUM(revenue) AS revenue, SUM(target) AS target, SUM(units_sold) AS units_sold,
           CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct
    FROM v_monthly_kpi
    WHERE year=? {m_filter} {r_filter}
    GROUP BY region, product_category
    ORDER BY region, revenue DESC
    """
    return query(sql, tuple(params))


# =============================================================================
# SALESFORCE PERFORMANCE
# =============================================================================

def get_salesperson_ranking(year: int, month: int | None = None,
                             region: str | None = None,
                             supervisor: str | None = None,
                             channel: str | None = None) -> pd.DataFrame:
    filters = ["year=?"]
    params: list = [year]
    if month:
        filters.append("month=?"); params.append(month)
    if region:
        filters.append("region=?"); params.append(region)
    if supervisor:
        filters.append("supervisor_name=?"); params.append(supervisor)
    if channel:
        filters.append("sales_channel=?"); params.append(channel)
    where = "WHERE " + " AND ".join(filters)
    sql = f"""
    SELECT
        salesperson_id, salesperson_name, region, subregion,
        supervisor_name, sales_channel,
        SUM(revenue)    AS revenue,
        SUM(target)     AS target,
        SUM(units_sold) AS units_sold,
        SUM(active_clients) AS active_clients,
        CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct
    FROM v_monthly_kpi
    {where}
    GROUP BY salesperson_id, salesperson_name, region, subregion,
             supervisor_name, sales_channel
    ORDER BY revenue DESC
    """
    return query(sql, tuple(params))


def get_supervisor_summary(year: int, month: int | None = None,
                            region: str | None = None) -> pd.DataFrame:
    filters = ["year=?"]
    params: list = [year]
    if month:
        filters.append("month=?"); params.append(month)
    if region:
        filters.append("region=?"); params.append(region)
    where = "WHERE " + " AND ".join(filters)
    sql = f"""
    SELECT supervisor_name, region,
           SUM(revenue) AS revenue, SUM(target) AS target,
           COUNT(DISTINCT salesperson_id) AS team_size,
           CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct
    FROM v_monthly_kpi
    {where}
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
    m_filter = "AND month=?" if month else ""
    r_filter = "AND region=?" if region else ""
    params = [year] + ([month] if month else []) + ([region] if region else [])
    sql = f"""
    SELECT product_category,
           SUM(revenue) AS revenue, SUM(target) AS target,
           SUM(units_sold) AS units_sold, SUM(weight_kg) AS weight_kg,
           CASE WHEN SUM(target)>0 THEN ROUND(SUM(revenue)/SUM(target)*100,2) ELSE NULL END AS achievement_pct
    FROM v_monthly_kpi
    WHERE year=? {m_filter} {r_filter}
    GROUP BY product_category
    ORDER BY revenue DESC
    """
    return query(sql, tuple(params))


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
           SUM(total_amount)   AS revenue,
           SUM(quantity)       AS units_sold,
           SUM(total_weight_kg) AS weight_kg,
           AVG(price_variance_pct) AS avg_price_variance_pct,
           COUNT(DISTINCT clientsd_id) AS active_clients
    FROM v_sales_base
    {where}
    GROUP BY product_category, product_subcategory, product_name, sku, is_innovation_product
    ORDER BY revenue DESC
    LIMIT {limit}
    """
    return query(sql, tuple(params))


def get_innovation_performance(year: int, month: int | None = None) -> pd.DataFrame:
    m_filter = "AND sale_month=?" if month else ""
    params = [year] + ([month] if month else [])
    sql = f"""
    SELECT
        is_innovation_product,
        product_category,
        SUM(total_amount)   AS revenue,
        SUM(quantity)       AS units_sold,
        COUNT(DISTINCT clientsd_id) AS active_clients,
        COUNT(DISTINCT sku) AS distinct_skus
    FROM v_sales_base
    WHERE sale_year=? {m_filter}
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


def get_seasonality_heatmap(year: int, region: str | None = None) -> pd.DataFrame:
    """Month x Weekday revenue heatmap data."""
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
                   prefix: str = "WHERE") -> tuple[str, list]:
    clauses = [f"year={year}"]
    params: list = []
    if month:
        clauses.append("month=?"); params.append(month)
    if regions:
        placeholders = ",".join(["?"] * len(regions))
        clauses.append(f"region IN ({placeholders})")
        params.extend(regions)
    if channels:
        placeholders = ",".join(["?"] * len(channels))
        clauses.append(f"sales_channel IN ({placeholders})")
        params.extend(channels)
    return (f"{prefix} " + " AND ".join(clauses)), params
