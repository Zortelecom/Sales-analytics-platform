MODEL (
  name staging.stg_salesteam_data,
  kind SCD_TYPE_2_BY_COLUMN (
    unique_key (salesperson_id),
    columns [region, subregion, sales_channel, supervisor_name]
  ),
  cron '@daily',
  grain (salesperson_key),
  owner analytics_team,
  storage_format 'parquet',
  audits (
    -- Surrogate key must be unique across ALL rows (including history).
    -- A collision here is the exact bug that was reported.
    unique_values(columns := (salesperson_key)),
    -- These fields are required for every row downstream.
    not_null(columns := (
      salesperson_key,
      salesperson_id,
      salesperson_name,
      region,
      subregion,
      sales_channel,
      supervisor_name
    ))
  )
);

SELECT
  @GENERATE_SURROGATE_KEY (
    TRIM(salesperson_id),
    TRIM(region),
    TRIM(subregion),
    TRIM(channel),
    TRIM(supervisor),
    hash_function := 'MD5_NUMBER_LOWER'
  ) AS salesperson_key,

  salesteam_ref_id,
  TRIM(salesperson_id)    AS salesperson_id,
  TRIM(fullname)          AS salesperson_name,
  TRIM(region)            AS region,
  TRIM(subregion)         AS subregion,
  TRIM(channel)           AS sales_channel,
  TRIM(supervisor)        AS supervisor_name

FROM raw.salesteam_data
WHERE TRIM(salesperson_id) IS NOT NULL
  AND TRIM(salesperson_id) != '';
