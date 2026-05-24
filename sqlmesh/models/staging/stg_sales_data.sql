MODEL (
  name staging.stg_sales_data,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column sale_date
  ),
  start '2025-01-01',
  cron '@daily',
  grain (sales_line_id),
  owner analytics_team,
  storage_format 'parquet',
  audits (
    -- Each sales line must appear exactly once in every loaded window.
    unique_values(columns := (sales_line_id)),

    -- All FK / measure columns that must never be null.
    not_null(columns := (
      sales_line_id,
      sale_date,
      sku,
      salesperson_id,
      quantity,
      sales_amount
    ), blocking := false),

    -- Quantities and amounts must be strictly positive.
    -- Negative values could indicate unprocessed credit notes that
    -- would silently distort revenue and weight totals.
    accepted_range(column := quantity,     min_v := 0, inclusive := false, blocking := false),
    accepted_range(column := sales_amount, min_v := 0, inclusive := false, blocking := false),
    accepted_range(column := unit_price,   min_v := 0, inclusive := false, blocking := false),
    
    assert_sales_data_is_fresh
  )
);

SELECT
  -- Primary key
  sales_line_id,
  
  -- Date parsing
  COALESCE(
    TRY_CAST(sale_date AS DATE), 
    strptime(sale_date, '%d/%m/%Y')::DATE 
  ) AS sale_date,
  
  -- Product identifiers
  TRIM(UPPER(sku))            AS sku,
  TRIM(product_name)          AS product_name,
  TRIM(UPPER(product_cat))           AS product_category,
  TRIM(UPPER(product_subcat))        AS product_subcategory,
  
  -- Measures
  TRY_CAST(qty AS DECIMAL(10, 2))         AS quantity,
  TRY_CAST(unit_price AS INTEGER)         AS unit_price,
  TRY_CAST(amount AS DECIMAL(12,2))       AS sales_amount,
  TRY_CAST(unit_weight AS DECIMAL(10,2))  AS unit_weight_kg,

  -- Salesperson identifiers
  TRIM(salesperson_id)  AS salesperson_id,
  TRIM(salesperson)     AS salesperson_name,
  TRIM(supervisor)      AS supervisor_name,
  TRIM(channel)         AS sales_channel,
  
  -- Client identifiers
  TRIM(sd_id)           AS clientsd_id,
  TRIM(sd_destocke)     AS sd_destocked,
  
  -- Geographic attributes
  TRIM(subregion)             AS subregion,
  TRIM(filename_subregion)    AS filename_subregion,
  
  -- Product flags
  CASE 
    WHEN LOWER(TRIM(is_innovation)) IN ('true', 'yes', '1', 'oui') THEN TRUE
    ELSE FALSE
  END AS is_innovation_product

FROM raw.sales_data
WHERE sale_date IS NOT NULL
  AND TRY_CAST(sale_date AS DATE) BETWEEN @start_date AND @end_date
  AND TRIM(sku) IS NOT NULL
  AND TRIM(sku) != ''
  AND (has_null_key = FALSE OR has_null_key IS NULL);
