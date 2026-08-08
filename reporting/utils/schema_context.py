# reporting/utils/schema_context.py
"""
Fixes applied (reporting-layer review):
  - get_schema_context() had no caching: every question typed into
    "Ask Your Data" re-ran 5 separate `SELECT * LIMIT 2` sample queries
    before the LLM call even started, adding latency and DB load for data
    that essentially never changes within a session. Wrapped in
    @st.cache_data.
  - The KP-SD / sell-in views (v_kp_sd_base, v_sellin_sellout_kpi) were
    completely absent from the schema description, so natural-language
    questions about sell-in, sell-out, or destocked SDs had no chance of
    being answered even though the data exists — the LLM was never told
    those views exist. Added them, and switched to the centralized,
    schema-qualified constants in reporting.utils.views instead of manually
    re-qualifying names inline.
"""
import streamlit as st
from reporting.utils.db import query
from reporting.config import DB_SCHEMA
from reporting.utils.views import (
    MONTHLY_KPI, SALES_BASE, WEEKLY_KPI, KP_SD_BASE, SELLIN_SELLOUT_KPI,
)


@st.cache_data(ttl=600, show_spinner=False)
def get_schema_context() -> str:
    """Return a compact schema description for LLM prompting."""
    # FIX: the dimension probes used unqualified names (e.g. "dim_salesperson"),
    # but dims live in the DB_SCHEMA ("bi") schema while views live in
    # DB_VIEWS_SCHEMA ("main"). The unqualified dim probes failed at runtime —
    # flashing st.error and returning empty samples to the LLM. Names are now
    # schema-qualified via config.
    views = [
        (MONTHLY_KPI, "Aggregated monthly revenue, target, units, weight by year/month/region/salesperson/category"),
        (SALES_BASE,  "Grain: one row per sales line (sell-out). Columns: sale_date, sale_year, sale_month, salesperson_id, salesperson_name, region, subregion, clientsd_id, client_name, sku, product_name, product_category, is_innovation_product, total_amount, quantity, total_weight_kg"),
        (WEEKLY_KPI,  "Weekly aggregation: sale_year, sale_month, week_of_month, revenue, units_sold"),
        (KP_SD_BASE,  "Grain: one row per KP-to-SD sell-in line. Columns: clientsd_id, client_name, sale_date, sale_year, sale_month, sku, is_destocked, source_asserted_destocked, destocked_flag_mismatch, total_amount, quantity"),
        (SELLIN_SELLOUT_KPI, "Sell-in (KP shipments) vs. sell-out (SD resale) by month × SD, with sell-through ratio. Use this for reconciliation and overstock questions."),
        (f"{DB_SCHEMA}.dim_salesperson",       "Salesperson master: salesperson_id, salesperson_name, supervisor_name, region, sales_channel"),
        (f"{DB_SCHEMA}.dim_products",          "Product master: sku, product_name, product_category, product_subcategory, is_innovation_product, unit_price"),
    ]
    lines = ["Available views and tables in DuckDB (schema-qualified as shown):"]
    for name, desc in views:
        # Fetch 2 sample rows for grounding
        try:
            sample = query(f"SELECT * FROM {name} LIMIT 2").to_dict(orient="records")
        except Exception:
            sample = []
        lines.append(f"\n### {name}\n{desc}\nSample rows: {sample}")
    lines.append("\nCurrency: XAF (integer, no decimals). Dates: YYYY-MM-DD.")
    return "\n".join(lines)
