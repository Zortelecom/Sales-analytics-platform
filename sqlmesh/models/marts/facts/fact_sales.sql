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
  partitioned_by (sale_year, sale_month),
  audits (
    -- Built-in: primary key integrity.
    unique_values(columns := (sales_line_id)),
    not_null(columns := (sales_line_id, sale_date, sku, salesperson_id)),

    -- Built-in: measures must be positive.
    accepted_range(column := total_amount, min_v := 0, inclusive := false),
    accepted_range(column := quantity,     min_v := 0, inclusive := false),

    -- Custom: FK orphan detection — see audits/*.
    -- Each audit returns rows that FAIL; a non-empty result blocks the run.
    assert_no_orphaned_salesperson,
    assert_no_orphaned_product,
    -- assert_no_orphaned_client,
    assert_amount_is_integer_xaf,

    -- Custom: amount coherence check.
    assert_amount_matches_qty_x_price
  )
);

SELECT
  -- Primary key
  s.sales_line_id,
  
  -- Date dimension FK
  CAST(STRFTIME(s.sale_date, '%Y%m%d') AS INTEGER) AS date_key,
  s.sale_date,
  EXTRACT(YEAR  FROM s.sale_date) AS sale_year,
  EXTRACT(MONTH FROM s.sale_date) AS sale_month,
  
  -- Product dimension FK
  p.product_key,
  s.sku,
  p.product_category,
  
  -- Salesperson dimension FK
  sp.salesperson_key,
  s.salesperson_id,
  sp.salesperson_name,
  sp.region,
  sp.subregion,
  sp.sales_channel,
  sp.supervisor_name,

  -- Client dimension FK
  c.clientsd_key,
  s.clientsd_id,
  
  -- MEASURES
  s.quantity,
  s.unit_price                    AS unit_price_actual,
  s.sales_amount                  AS total_amount,
  
  -- Dimension reference values for variance analysis
  p.unit_price                    AS unit_price_standard,
  s.unit_weight_kg                AS unit_weight_actual,
  p.unit_weight_kg                AS unit_weight_standard,
  
  -- Calculated measures
  s.quantity * COALESCE(p.unit_weight_kg, s.unit_weight_kg, 0) AS total_weight_kg,
  
  -- Price variance
  CASE 
    WHEN p.unit_price > 0 AND s.unit_price > 0
    THEN ROUND(((s.unit_price - p.unit_price) / p.unit_price) * 100, 2)
    ELSE 0
  END AS price_variance_pct

FROM staging.stg_sales_data s

-- Product dimension (SCD Type 2 join)
LEFT JOIN marts.dim_products p
  ON s.sku = p.sku
  AND s.sale_date >= p.valid_from
  AND (s.sale_date < p.valid_to OR p.valid_to IS NULL)

-- Salesperson dimension (SCD Type 2 join)
LEFT JOIN marts.dim_salesperson sp
  ON s.salesperson_id = sp.salesperson_id
  AND s.sale_date >= sp.valid_from
  AND (s.sale_date < sp.valid_to OR sp.valid_to IS NULL)

-- Client dimension (SCD Type 2 join)
LEFT JOIN marts.dim_clientsd c
  ON s.clientsd_id = c.sd_id
  AND s.sale_date >= c.valid_from
  AND (s.sale_date < c.valid_to OR c.valid_to IS NULL);
