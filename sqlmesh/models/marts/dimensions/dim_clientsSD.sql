MODEL (
  name marts.dim_clientsd,
  kind SCD_TYPE_2_BY_COLUMN (
    unique_key sd_id,
    columns (region, subregion, city, key_player, is_destocked),
    time_data_type TIMESTAMP
  ),
  cron '@daily',
  grain (client_key),
  owner analytics_team,
  storage_format 'parquet'
);

WITH clients_with_versions AS (
  SELECT
    sd_id,
    sd_name,
    region,
    subregion,
    city,
    key_player,
    phone_number,
    is_destocked
  FROM staging.stg_clientsd_data
)

SELECT
  -- Surrogate key - Integer for Power BI relationships
  ROW_NUMBER() OVER (ORDER BY sd_id, CURRENT_TIMESTAMP) AS client_key,
  
  -- Natural key (business key)
  sd_id,
  
  -- Client attributes
  sd_name,
  region,
  subregion,
  city,
  key_player,
  phone_number,
  is_destocked
FROM clients_with_versions;