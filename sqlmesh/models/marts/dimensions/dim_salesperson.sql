MODEL (
  name marts.dim_salesperson,
  kind SCD_TYPE_2_BY_COLUMN (
    unique_key salesperson_id,
    columns (region, subregion, sales_channel, supervisor_name),
    time_data_type TIMESTAMP
  ),
  cron '@daily',
  grain (salesperson_key),
  owner analytics_team,
  storage_format 'parquet'
);

WITH salespersons_with_versions AS (
  SELECT
    salesperson_id,
    salesperson_name,
    region,
    subregion,
    sales_channel,
    supervisor_name
  FROM staging.stg_salesteam_data
)

SELECT
  -- Surrogate key - Integer for Power BI relationships
  ROW_NUMBER() OVER (ORDER BY salesperson_id, CURRENT_TIMESTAMP) AS salesperson_key,
  
  -- Natural key (business key)
  salesperson_id,
  
  -- Salesperson attributes
  salesperson_name,
  region,
  subregion,
  sales_channel,
  supervisor_name
FROM salespersons_with_versions;