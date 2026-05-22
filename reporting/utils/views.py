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