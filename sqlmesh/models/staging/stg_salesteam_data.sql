MODEL (
  name staging.stg_salesteam_data,
  kind FULL,
  cron '@daily',
  grain (salesperson_id, effective_from),
  owner analytics_team,
  storage_format 'parquet',
  audits (

    unique_combination_of_columns(columns := (salesperson_id, effective_from)),
    -- These fields are required for every row downstream.
    not_null(columns := (
      salesperson_id,
      salesperson_name,
      region,
      subregion,
      sales_channel,
      supervisor_name,
      effective_from
    ))
  )
);

SELECT
  salesteam_ref_id,
  TRIM(salesperson_id)    AS salesperson_id,
  TRIM(fullname)          AS salesperson_name,
  TRIM(region)            AS region,
  TRIM(subregion)         AS subregion,
  TRIM(channel)           AS sales_channel,
  TRIM(supervisor)        AS supervisor_name,
  TRY_CAST(effective_from AS TIMESTAMP) AS effective_from

FROM raw.salesteam_data
WHERE TRIM(salesperson_id) IS NOT NULL
  AND TRIM(salesperson_id) != ''
  -- A row that can't be placed in time can't be historized correctly
  -- downstream, so it's filtered here where it's auditable (not_null above
  -- will still flag it if this ever fires).
  AND TRY_CAST(effective_from AS TIMESTAMP) IS NOT NULL;