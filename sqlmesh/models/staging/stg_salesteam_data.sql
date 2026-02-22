MODEL (
  name staging.stg_salesteam_data,
  kind SCD_TYPE_2_BY_COLUMN (
    unique_key (salesperson_id),
    columns [region, subregion, sales_channel, supervisor_name]
  ),
  cron '@daily',
  grain (salesperson_key),
  owner analytics_team,
  storage_format 'parquet'
);

SELECT
  @GENERATE_SURROGATE_KEY (
    TRIM(region),
    TRIM(subregion),
    TRIM(channel),
    TRIM(supervisor),
    hash_function := 'MD5_NUMBER_LOWER'
  ) AS salesperson_key,

  salesteam_ref_id,
  TRIM(salesperson_id) AS salesperson_id,
  TRIM(fullname) AS salesperson_name,
  TRIM(region) AS region,
  TRIM(subregion) AS subregion,
  TRIM(channel) AS sales_channel,
  TRIM(supervisor) AS supervisor_name

FROM raw.salesteam_data
WHERE TRIM(salesperson_id) IS NOT NULL;