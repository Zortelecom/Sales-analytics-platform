# reporting/utils/schema_context.py
from reporting.utils.db import query

def get_schema_context() -> str:
    """Return a compact schema description for LLM prompting."""
    views = [
        ("v_monthly_kpi",   "Aggregated monthly revenue, target, units, weight by year/month/region/salesperson/category"),
        ("v_sales_base",    "Grain: one row per sales line. Columns: sale_date, sale_year, sale_month, salesperson_id, salesperson_name, region, subregion, clientsd_id, client_name, sku, product_name, product_category, is_innovation_product, total_amount, quantity, total_weight_kg"),
        ("v_weekly_kpi",    "Weekly aggregation: sale_year, sale_month, week_of_month, revenue, units_sold"),
        ("dim_salesperson",  "Salesperson master: salesperson_id, salesperson_name, supervisor_name, region, sales_channel"),
        ("dim_products",     "Product master: sku, product_name, product_category, product_subcategory, is_innovation_product, unit_price"),
    ]
    lines = ["Available views (all in DuckDB, referenced without schema prefix):"]
    for name, desc in views:
        # Fetch 2 sample rows for grounding
        try:
            sample = query(f"SELECT * FROM {name} LIMIT 2").to_dict(orient="records")
        except Exception:
            sample = []
        lines.append(f"\n### {name}\n{desc}\nSample rows: {sample}")
    lines.append("\nCurrency: XAF (integer, no decimals). Dates: YYYY-MM-DD.")
    return "\n".join(lines)