/*
  Sell-out staging: SD -> market.

  (2026-08) Four changes.

  1. @start_date/@end_date -> @start_ds/@end_ds.
     time_column is sale_date, a DATE. @start_ds/@end_ds are the DATE-shaped
     interval macros and are what dim_salesperson, dim_clientsd and fact_sales
     already use. Two conventions in one project is a trap.

  2. channel -> sales_channel_sheet.
     The workbook's Channel column arrives by lookup against Ref_Salesteam,
     with no date awareness -- so when Nzongue Charmita moved from traditional
     trade to GMS, every one of her OLD lines started reporting GMS. The
     authoritative channel is dim_salesperson resolved at sale_date, which
     fact_sales now uses for the price tier. This column is kept only so the
     drift is measurable; it must not drive a measure. Same for
     salesperson_name and supervisor_name, renamed for the same reason.

  3. Provenance is carried through.
     _source_file, _sheet_name and _row_num travel from landing into staging
     and on into fact_sales. Without them a failing audit row can say "12 rows
     have no product_key" but not WHICH WORKBOOK, and the person who has to fix
     it is a supervisor, not a SQL user. filename_subregion is a slug derived
     from the filename; _source_file is the file.

  4. has_null_key is now the only null guard needed.
     The old `WHERE sale_date IS NOT NULL AND TRIM(sku) != ''` did nothing for
     most of the pipeline's life: the extractor cast every column to str, so a
     missing cell arrived as the STRING 'nan', which is not NULL and not ''.
     Landing preserves real NULLs, so those guards now bite -- expect the row
     count to drop. has_null_key is the column the extractor actually
     maintains, and for KP sources it covers sd_id too. The explicit checks are
     kept as belt-and-braces, but has_null_key is the one that means something.
*/
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

    not_null(columns := (
      sales_line_id,
      sale_date,
      sku,
      salesperson_id,
      quantity,
      sales_amount
    ), blocking := false),

    -- Negative values usually mean an unprocessed credit note, which would
    -- silently distort revenue and weight totals.
    accepted_range(column := quantity,     min_v := 0, inclusive := false, blocking := false),
    accepted_range(column := sales_amount, min_v := 0, inclusive := false, blocking := false),
    accepted_range(column := unit_price,   min_v := 0, inclusive := false, blocking := false)
  )
);

SELECT
  sales_line_id,

  -- Provenance. Traces a failing row to a workbook, a supervisor's tab and a
  -- position within it. Prefixed with _ so nothing mistakes them for business
  -- columns; carried into fact_sales for the data-quality report.
  _source_file  AS source_file,
  _sheet_name   AS sheet_name,
  _row_num      AS source_row_num,

  -- sale_date now arrives typed from landing. TRY_CAST on a DATE is a no-op;
  -- the strptime fallback stays for the case where a supervisor formats a cell
  -- as text and the extractor could not parse it as a date.
  COALESCE(
    TRY_CAST(sale_date AS DATE),
    TRY_STRPTIME(CAST(sale_date AS VARCHAR), '%d/%m/%Y')::DATE
  ) AS sale_date,

  -- Product identifiers
  TRIM(UPPER(sku))                    AS sku,
  TRIM(product_name)                  AS product_name_sheet,
  TRIM(UPPER(product_cat))            AS product_category_sheet,
  TRIM(UPPER(product_subcat))         AS product_subcategory_sheet,

  -- Measures
  TRY_CAST(qty AS DECIMAL(10,2))          AS quantity,
  TRY_CAST(unit_price AS INTEGER)         AS unit_price,
  TRY_CAST(amount AS DECIMAL(12,2))       AS sales_amount,
  TRY_CAST(unit_weight AS DECIMAL(10,2))  AS unit_weight_kg,

  -- Salesperson. These three are workbook lookups with no date awareness --
  -- fact_sales takes the authoritative values from dim_salesperson at
  -- sale_date. Kept for drift detection only.
  TRIM(salesperson_id)  AS salesperson_id,
  TRIM(salesperson)     AS salesperson_name_sheet,
  TRIM(supervisor)      AS supervisor_name_sheet,
  TRIM(channel)         AS sales_channel_sheet,

  -- Client identifiers
  TRIM(sd_id)           AS clientsd_id,
  TRIM(sd_destocke)     AS sd_name_sheet,

  -- Geography. filename_subregion comes from the workbook name and cannot be
  -- left blank by a supervisor, unlike the subregion column.
  TRIM(subregion)           AS subregion_sheet,
  TRIM(filename_subregion)  AS filename_subregion,

  CASE
    WHEN LOWER(TRIM(CAST(is_innovation AS VARCHAR)))
         IN ('true', 'yes', '1', 'oui') THEN TRUE
    ELSE FALSE
  END AS is_innovation_product_sheet

FROM raw.sales_data
WHERE (has_null_key = FALSE OR has_null_key IS NULL)
  AND sale_date IS NOT NULL
  AND TRIM(sku) IS NOT NULL
  AND TRIM(sku) != ''
  AND COALESCE(
        TRY_CAST(sale_date AS DATE),
        TRY_STRPTIME(CAST(sale_date AS VARCHAR), '%d/%m/%Y')::DATE
      ) BETWEEN @start_ds AND @end_ds;