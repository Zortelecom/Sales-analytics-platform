"""
reporting/utils/queries.py
All SQL query builders for the reporting pages.
Returns pd.DataFrames via db.query().


If you add a query, keep using bare names and go through db.query().
"""
from __future__ import annotations
import pandas as pd
from reporting.utils.db import object_exists, query


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


def get_top_regions(year: int, month: int | None = None,
                    channels: list[str] | None = None,
                    limit: int = 10) -> pd.DataFrame:
    """Top regions by revenue. active_clients from v_sales_base to avoid double-count.
    Now propagates the channel filter (§2.4).
    """
    kpi_filters, kpi_params = _build_filters(year=year, month=month, channels=channels, prefix="WHERE")
    # FIX: the clients CTE was built WITHOUT `channels`, so active_clients
    # ignored the channel filter while revenue/target respected it — the
    # docstring (§2.4) and get_regional_summary / get_category_performance
    # all propagate channels to both CTEs.
    base_filters, base_params = _build_filters(year=year, month=month, channels=channels,
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

def get_regional_summary(year: int, month: int | None = None,
                          channels: list[str] | None = None) -> pd.DataFrame:
    """Revenue/target by region × subregion.
    active_clients from v_sales_base to avoid cross-salesperson double-count.
    Clients CTE now uses _build_filters for consistency (§2.3).
    """
    kpi_filters, kpi_params = _build_filters(year=year, month=month,
                                              channels=channels, prefix="WHERE")
    base_filters, base_params = _build_filters(year=year, month=month,
                                                channels=channels, prefix="WHERE",
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


def get_region_category_breakdown(year: int, month: int | None = None,
                                   region: str | None = None,
                                   channels: list[str] | None = None) -> pd.DataFrame:
    """Category breakdown by region. Now accepts channels filter (§3.6)."""
    filters, params = _build_filters(year=year, month=month, channels=channels, prefix="WHERE")
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
                            region: str | None = None,
                            channels: list[str] | None = None) -> pd.DataFrame:
    """Supervisor summary. Now propagates channels filter (§2.4)."""
    filters, params = _build_filters(year=year, month=month, channels=channels, prefix="WHERE")
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
                              region: str | None = None,
                              channels: list[str] | None = None) -> pd.DataFrame:
    """Category KPIs. UPPER() removed — category filter in page 4 relies on
    exact case-match; normalise at staging layer if needed, not here.
    active_clients from v_sales_base to avoid cross-salesperson double-count.
    Clients CTE now uses _build_filters for param consistency (§2.5).
    """
    kpi_filters, kpi_params = _build_filters(year=year, month=month, channels=channels, prefix="WHERE")
    base_filters, base_params = _build_filters(year=year, month=month, channels=channels,
                                                prefix="WHERE",
                                                year_col="sale_year", month_col="sale_month")
    r_clause = ""
    if region:
        r_clause = "AND region=?"
        kpi_params = list(kpi_params) + [region]
        base_params = list(base_params) + [region]

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
        {base_filters} {r_clause}
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
    LIMIT ?
    """
    return query(sql, tuple(params + [limit]))


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


def _view_exists(view_name: str) -> bool:
    """True when a view/table exists in the bi schema.
    """
    return object_exists(view_name, "bi")


def get_quarterly_summary(year: int, region: str | None = None) -> pd.DataFrame:
    """Quarterly summary with graceful fallback to v_monthly_kpi (§3.8)."""
    r_filter = "AND region=?" if region else ""
    params = [year] + ([region] if region else [])
    if _view_exists("v_quarterly_kpi"):
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
    else:
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
        WHERE year=? {r_filter}
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

def get_sellin_years() -> list[int]:
    df = query("SELECT DISTINCT year FROM v_sellin_sellout_kpi ORDER BY year DESC")
    return [int(v) for v in df.iloc[:, 0].dropna()] if not df.empty else []


def get_sellin_months(year: int) -> list[int]:
    df = query(
        "SELECT DISTINCT month FROM v_sellin_sellout_kpi "
        "WHERE year=? ORDER BY month",
        (year,),
    )
    return [int(v) for v in df.iloc[:, 0].dropna()] if not df.empty else []


def get_sellin_sellout(year: int, month: int | None = None,
                       subregions: list[str] | None = None) -> pd.DataFrame:
    """Sell-in vs sell-out by SD and product for the period."""
    clauses, params = ["year=?"], [year]
    if month:
        clauses.append("month=?"); params.append(month)
    if subregions:
        # Requires bi_views_rls_patch.sql — subregion is marked [PATCH] on this
        # view in rls.py. If plan hasn't rebuilt with it, this raises a binder
        # error rather than filtering silently, which is the behaviour you want.
        clauses.append(f"subregion IN ({','.join(['?']*len(subregions))})")
        params.extend(subregions)
    sql = f"SELECT * FROM v_sellin_sellout_kpi WHERE {' AND '.join(clauses)}"
    return query(sql, tuple(params))


def get_destocked_flag_mismatches(year: int, month: int | None = None) -> pd.DataFrame:
    """Sell-in rows whose source-asserted destockage disagrees with the SD flag.

    ⚠ Column names unverified: this filters v_kp_sd_base on sale_year/sale_month
    while v_sellin_sellout_kpi uses year/month. Confirm with
    `SELECT * FROM v_kp_sd_base LIMIT 0` before trusting it.
    """
    clauses, params = ["sale_year=?"], [year]
    if month:
        clauses.append("sale_month=?"); params.append(month)
    clauses.append("destocked_flag_mismatch")
    sql = f"""
    SELECT clientsd_id, client_name, sale_date, sku, source_asserted_destocked
    FROM v_kp_sd_base
    WHERE {' AND '.join(clauses)}
    """
    return query(sql, tuple(params))

# =============================================================================
# HELPERS
# =============================================================================

def _build_filters(year: int,
                   month: int | None = None,
                   regions: list[str] | None = None,
                   subregions: list[str] | None = None,   # NEW
                   channels: list[str] | None = None,
                   prefix: str = "WHERE",
                   year_col: str = "year",
                   month_col: str = "month") -> tuple[str, list]:
    clauses = [f"{year_col}=?"]
    params: list = [year]
    if month:
        clauses.append(f"{month_col}=?"); params.append(month)
    if regions:
        clauses.append(f"region IN ({','.join(['?']*len(regions))})")
        params.extend(regions)
    if subregions:
        # `subregion` exists on v_monthly_kpi, v_sales_base, v_weekly_kpi and
        # v_quarterly_kpi — see SCOPE_COLUMNS in rls.py, which is the same
        # audit. Composes with RLS: this is an outer WHERE, RLS rewrites the
        # object beneath it, so a scoped user gets the intersection.
        clauses.append(f"subregion IN ({','.join(['?']*len(subregions))})")
        params.extend(subregions)
    if channels:
        clauses.append(f"sales_channel IN ({','.join(['?']*len(channels))})")
        params.extend(channels)
    return (f"{prefix} " + " AND ".join(clauses)), params