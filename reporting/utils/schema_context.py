# reporting/utils/schema_context.py
"""
(2026-08) Three fixes, one of them a cross-user data leak.

  1. CACHE LEAK (security). @st.cache_data is process-wide and this function
     took NO scope argument, while the sample rows it embeds come from
     query(), which applies row-level security. So the FIRST user to open
     "Ask Your Data" populated the cache with rows filtered to THEIR scope,
     and every user for the next 10 minutes got that prompt -- an admin's
     national sample served to a supervisor, or one supervisor's clients to
     another. db.py partitions its cache with scope_key for exactly this
     reason; this function bypassed it. Fixed by taking scope_key and by not
     sampling rows at all (see 3).

  2. Schema qualification. The dim probes used f"{DB_SCHEMA}.dim_salesperson",
     which resolved to bi.dim_salesperson. In the lake the dimensions are in
     marts__<env> -- the prefix is now dropped and db.py's search path
     resolves both. reporting/utils/views.py needs the same treatment.

  3. Stale and misleading column descriptions. v_kp_sd_base no longer has
     is_destocked / source_asserted_destocked / destocked_flag_mismatch, and
     dim_products.unit_price is the TRADITIONAL TRADE tier only. Describing it
     as "unit price" invited the model to compare GMS and sell-in lines against
     a TT price -- the exact error the pricing rework removed from the audits.

Earlier fixes (reporting-layer review):
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

# Bare names: db.get_connection() sets a search path over bi__<env>,
# marts__<env> and meta__<env>, so these resolve in any environment. Do NOT
# describe landing.* or raw.* here -- they carry _source_path, _file_sha256
# and supervisor sheet names, i.e. the server's directory layout and staff
# names, and this text is sent to a third-party API.
MONTHLY_KPI = "v_monthly_kpi"
SALES_BASE = "v_sales_base"
WEEKLY_KPI = "v_weekly_kpi"
KP_SD_BASE = "v_kp_sd_base"
SELLIN_SELLOUT_KPI = "v_sellin_sellout_kpi"


@st.cache_data(ttl=600, show_spinner=False)
def _build(scope_key: str) -> str:
    """
    Schema description for LLM prompting.

    `scope_key` is not read in the body -- it exists to partition the cache,
    exactly as in db._run. Note it is NOT named _scope_key: Streamlit EXCLUDES
    leading-underscore parameters from the cache key, which would silently
    reinstate the leak this parameter prevents.
    """
    views = [
        (MONTHLY_KPI, "Aggregated monthly revenue, target, units, weight by year/month/region/salesperson/category"),
        (SALES_BASE,  "Grain: one row per sales line (sell-out). Columns: sale_date, sale_year, sale_month, salesperson_id, salesperson_name, region, subregion, clientsd_id, client_name, sku, product_name, product_category, is_innovation_product, total_amount, quantity, total_weight_kg"),
        (WEEKLY_KPI,  "Weekly aggregation: sale_year, sale_month, week_of_month, revenue, units_sold"),
        (KP_SD_BASE,  "Grain: one row per KP-to-SD sell-in line (sell-in). Columns: "
                      "clientsd_id, client_name, region, subregion, supervisor_name, "
                      "sale_date, sale_year, sale_month, sku, product_category, "
                      "quantity, total_amount, "
                      "kp_name and kp_name_normalized (the Key Player on the LINE -- use "
                      "kp_name_normalized for KP rollups, not the SD's key_player), "
                      "destockage_channel (BOOLEAN: was this line filed as destocke), "
                      "is_destocked_sd (whether the SD is a destocking client at all), "
                      "destockage_channel_conflict (the two disagree), "
                      "price_tier (always 'SD' here), unit_price_effective "
                      "(total_amount/quantity -- the price actually charged), "
                      "unit_price_standard (the SD-tier reference price), "
                      "unit_price_sheet (a traditional-trade lookup copied into the "
                      "workbook; for traceability only -- NEVER aggregate it)"),
        (SELLIN_SELLOUT_KPI, "Sell-in (KP shipments) vs. sell-out (SD resale) by month × SD, with sell-through ratio. Use this for reconciliation and overstock questions."),
        ("dim_salesperson", "Salesperson master (SCD Type 2 -- valid_from/valid_to, "
                            "so one salesperson_id can have several rows): "
                            "salesperson_id, salesperson_name, supervisor_name, region, "
                            "subregion, sales_channel"),
        ("dim_products",    "Product master (SCD Type 2): sku, product_name, "
                            "product_category, product_subcategory, is_innovation_product, "
                            "unit_price. WARNING: unit_price is the TRADITIONAL TRADE "
                            "price only. GMS and sell-in are priced differently -- join "
                            "dim_product_price on (sku, price_tier) for those, or use "
                            "unit_price_effective on the fact."),
        ("dim_product_price", "Price per (sku, price_tier, valid_from..valid_to). "
                              "price_tier is 'TT' (traditional trade), 'GMS' (grande et "
                              "moyenne surface) or 'SD' (sell-in, KP to sub-distributor). "
                              "A product absent for a tier is not sold through it."),
    ]
    lines = ["Available views and tables in DuckDB. Reference them by the bare "
             "names below -- the connection's search path resolves them."]
    for name, desc in views:
        lines.append(f"\n### {name}\n{desc}")

    # Sample rows are deliberately NOT included any more. They were two real
    # rows per object -- client names, SKUs and amounts -- sent to a
    # third-party API on every cache miss, and they were the vector for the
    # cross-user cache leak described in the header. The column descriptions
    # above ground the model adequately without shipping customer data.
    lines.append(
        "\nCurrency: XAF (integer, no decimals). Dates: YYYY-MM-DD."
        "\nThe SCD Type 2 dimensions carry valid_from/valid_to: join a fact on "
        "the key AND `fact.sale_date >= dim.valid_from AND (fact.sale_date < "
        "dim.valid_to OR dim.valid_to IS NULL)`, otherwise a salesperson who "
        "changed region is counted twice."
    )
    return "\n".join(lines)


def get_schema_context() -> str:
    """Schema description for the current user's scope."""
    from reporting.utils.db import _principal, _scope_fingerprint
    return _build(_scope_fingerprint(_principal()))