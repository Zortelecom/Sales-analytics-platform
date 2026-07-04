MODEL (
  name marts.dim_salesperson,
  kind SCD_TYPE_2_BY_COLUMN (
    unique_key (salesperson_id),
    columns [
      region,
      subregion,
      sales_channel,
      supervisor_name
    ],
    updated_at_name effective_from,
    batch_size 1
  ),
  start '2025-01-01',
  cron '@monthly',
  grain (salesperson_key),
  owner analytics_team,
  storage_format 'parquet',
  audits (
    -- Built-in: primary key integrity.
    unique_values(columns := (salesperson_key)),
    not_null(columns := (salesperson_key, salesperson_id)),

    -- Custom: SCD window overlap check — see audits/*.
    -- SQLMesh manages valid_from/valid_to internally for this model kind,
    -- so true overlaps shouldn't occur, but this stays on as a regression
    -- guard now that the historization logic lives here.
    assert_no_overlapping_scd_windows(
        key            := salesperson_id,
        surrogate_key  := salesperson_key
    )
  )
);

SELECT
  -- salesperson_key is hashed from (salesperson_id, effective_from) rather
  -- than the attribute columns. valid_from (the SQLMesh-managed column)
  -- doesn't exist yet at this point in the query -- it's computed by
  -- SQLMesh after this SELECT runs -- but effective_from is available here
  -- and uniquely identifies each version. This is also what fixes the
  -- original collision bug: if a salesperson's attributes ever revert to a
  -- prior combination (e.g. region A -> B -> A), the two A-windows now get
  -- different keys instead of colliding on the same hash.
  @GENERATE_SURROGATE_KEY(
    TRIM(salesperson_id),
    region,
    subregion,
    sales_channel,
    supervisor_name,
    CAST(effective_from AS TEXT),
    hash_function := 'MD5_NUMBER_LOWER'
  ) AS salesperson_key,

  salesperson_id,
  salesperson_name,
  region,
  subregion,
  sales_channel,
  supervisor_name,
  effective_from

FROM staging.stg_salesteam_data

WHERE CAST(effective_from AS DATE) BETWEEN @start_ds AND @end_ds;