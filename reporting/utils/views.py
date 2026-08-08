#Centralised view name constants so queries.py stays in sync with bi_views.sql.

from reporting.config import DB_VIEWS_SCHEMA as _S

SALES_BASE        = f"{_S}.v_sales_base"
MONTHLY_KPI       = f"{_S}.v_monthly_kpi"
WEEKLY_KPI        = f"{_S}.v_weekly_kpi"
QUARTERLY_KPI     = f"{_S}.v_quarterly_kpi"
TARGETS_BASE      = f"{_S}.v_targets_base"
EXECUTIVE_SUMMARY = f"{_S}.v_executive_summary"
YOY_COMPARISON    = f"{_S}.v_yoy_comparison"
REGIONAL_KPI      = f"{_S}.v_regional_kpi"
SALESPERSON_KPI   = f"{_S}.v_salesperson_kpi"
PRODUCT_KPI       = f"{_S}.v_product_kpi"
INNOVATION_KPI    = f"{_S}.v_innovation_kpi"
CLIENT_KPI        = f"{_S}.v_client_kpi"

# Added (reporting-layer review): these four KP-SD / sell-in views existed in
# bi_views.sql and were already used — but only as hardcoded, unqualified
# strings in reporting/pages/8_Sell_In_Sell_Out.py, bypassing the
# centralization this module exists to provide. Import these instead.
KP_SD_BASE          = f"{_S}.v_kp_sd_base"
KP_SD_MONTHLY_KPI   = f"{_S}.v_kp_sd_monthly_kpi"
KP_PERFORMANCE_KPI  = f"{_S}.v_kp_performance_kpi"
SELLIN_SELLOUT_KPI  = f"{_S}.v_sellin_sellout_kpi"

# NOTE: queries.py itself does not import from this module — every query
# builder there references bare view names (e.g. "FROM v_monthly_kpi")
# instead of these schema-qualified constants. That works today only because
# DB_VIEWS_SCHEMA defaults to "main", DuckDB's default search-path schema.
# If SERVING_DB_VIEWS_SCHEMA is ever overridden, every query in queries.py
# would break silently while this module's own constants keep working. See
# REPORTING_IMPROVEMENT_PLAN.md for the recommended fix.
