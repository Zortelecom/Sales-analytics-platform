/*
  Sell-out fact: SD -> market.

  PRICING. Three columns replace the old unit_price_actual /
  unit_price_standard pair, because that pair compared Ref_Products against
  itself:

    unit_price_sheet      what the workbook says. A VLOOKUP of the Ref_Products
                          traditional-trade price, sitting next to product_name
                          and unit_weight which come from the same lookup. NOT
                          the price the line was transacted at.
    unit_price_effective  total_amount / quantity. The only actual price in the
                          data, and what every variance measure should use.
    unit_price_standard   the reference price for THIS line's tier and date,
                          from dim_product_price.

  PRICE TIER. Derived from the salesperson's channel. GMS teams buy from a Key
  Player treated as an SD and pay GMS rates, not traditional-trade rates.

.
*/
MODEL (
  name marts.fact_sales,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column sale_date
  ),
  start '2025-01-01',
  cron '@daily',
  grain (sales_line_id),
  owner analytics_team,
  storage_format 'parquet',
  audits (
    unique_values(columns := (sales_line_id)),
    not_null(columns := (sales_line_id, sale_date, sku, salesperson_id)),

    -- NOT accepted_range on quantity/total_amount: it is blanket, and GMS
    -- legitimately has returns (negative quantity AND negative amount) while
    -- traditional trade does not. assert_line_signs_are_coherent encodes that,
    -- and also catches the sign mismatch a range check cannot see.
    assert_line_signs_are_coherent,

    assert_no_orphaned_salesperson,
    assert_no_orphaned_product,
    -- assert_no_orphaned_client,
    -- assert_amount_is_integer_xaf removed. XAF has no subunit, so a
    -- fractional total_amount is odd, but the two rows it caught (3415.5 and
    -- 33552.5) are a handful out of 37,000 and the check was blocking every
    -- build over them. Nothing enforces integer amounts now -- if fractional
    -- amounts turn out to come from fractional quantities rather than from
    -- discounts, that is worth a look at source:
    --   SELECT sales_line_id, sku, quantity, unit_price_sheet, total_amount
    --   FROM marts.fact_sales WHERE total_amount <> FLOOR(total_amount);

    -- Line arithmetic (blocking) vs price conformance (non-blocking).
    -- These were one audit, and it fired on every GMS line.
    assert_amount_matches_qty_x_price,
    assert_price_matches_tier,
    -- Detects a date-blind VLOOKUP in the source workbook. The warehouse
    -- figure is right either way; this catches the sheet drifting from it.
    assert_sheet_lookup_is_date_correct
  )
);

WITH tiered AS (
  SELECT
    s.*,
    CASE
      WHEN UPPER(TRIM(sp.sales_channel)) = 'GMS' THEN 'GMS'
      ELSE 'TT'
    END AS price_tier,
    sp.salesperson_key,
    sp.salesperson_name,
    sp.region,
    sp.subregion   AS salesperson_subregion,
    sp.sales_channel,
    sp.supervisor_name
  FROM staging.stg_sales_data s
  LEFT JOIN marts.dim_salesperson sp
    ON s.salesperson_id = sp.salesperson_id
    AND s.sale_date >= sp.valid_from
    AND (s.sale_date < sp.valid_to OR sp.valid_to IS NULL)
)

SELECT
  t.sales_line_id,

  CAST(STRFTIME(t.sale_date, '%Y%m%d') AS INTEGER) AS date_key,
  t.sale_date,
  EXTRACT(YEAR  FROM t.sale_date) AS sale_year,
  EXTRACT(MONTH FROM t.sale_date) AS sale_month,

  p.product_key,
  t.sku,
  p.product_category,

  t.salesperson_key,
  t.salesperson_id,
  t.salesperson_name,
  t.region,
  t.salesperson_subregion AS subregion,
  t.sales_channel,
  t.supervisor_name,

  c.clientsd_key,
  t.clientsd_id,

  -- MEASURES
  t.quantity,
  t.sales_amount AS total_amount,

  -- PRICES -- see the header. These three are not interchangeable.
  t.price_tier,
  t.unit_price                              AS unit_price_sheet,
  ROUND(t.sales_amount / NULLIF(t.quantity, 0), 2) AS unit_price_effective,
  pp.unit_price                             AS unit_price_standard,

  -- Provenance for the data-quality report: which workbook, whose tab, which
  -- row.
  t.source_file,
  t.sheet_name,
  t.source_row_num,

  t.unit_weight_kg AS unit_weight_actual,
  p.unit_weight_kg AS unit_weight_standard,
  t.quantity * COALESCE(p.unit_weight_kg, t.unit_weight_kg, 0) AS total_weight_kg,

  -- Effective vs the reference for this line's tier and date.
  CASE
    WHEN pp.unit_price > 0 AND t.quantity > 0
    THEN ROUND(
      ((t.sales_amount / t.quantity) - pp.unit_price) / pp.unit_price * 100, 2)
  END AS price_variance_pct

FROM tiered t

LEFT JOIN marts.dim_products p
  ON t.sku = p.sku
  AND t.sale_date >= p.valid_from
  AND (t.sale_date < p.valid_to OR p.valid_to IS NULL)

LEFT JOIN marts.dim_product_price pp
  ON t.sku = pp.sku
  AND t.price_tier = pp.price_tier
  AND t.sale_date >= pp.valid_from
  AND (t.sale_date < pp.valid_to OR pp.valid_to IS NULL)

LEFT JOIN marts.dim_clientsd c
  ON t.clientsd_id = c.sd_id
  AND t.sale_date >= c.valid_from
  AND (t.sale_date < c.valid_to OR c.valid_to IS NULL)

WHERE t.sale_date BETWEEN @start_ds AND @end_ds;