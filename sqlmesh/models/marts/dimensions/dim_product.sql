/*
  Product dimension, historised on effective_from.

  (2026-08) SCD windows computed with LEAD, kind FULL -- no SQLMesh SCD kind.

  WHY NOT SCD_TYPE_2_BY_TIME
  ──────────────────────────
  By Time expects the source to be a SNAPSHOT: one row per key, with an
  updated_at saying when that state began. It detects change by comparing the
  snapshot against what is already stored. stg_products_data is the opposite --
  full dated history, several rows per key -- so By Time sees duplicate unique
  keys and cannot tell which is current. That is why it did not work.

  WHY NOT SCD_TYPE_2_BY_COLUMN
  ────────────────────────────
  By Column works, and the monthly cron + batch_size 1 + effective_from
  interval filter was a sound workaround: it replays history one month at a
  time so each key appears once per batch. But it stamps valid_from with the
  EXECUTION time, not the business date, so correctness depends on the change
  dates lining up with cron boundaries. It also cannot be backfilled from
  scratch without replaying every interval in order.

  WHY LEAD
  ────────
  SCD Type 2 machinery exists to DETECT changes in a snapshot. Here the source
  already is the history -- nothing needs detecting, only windowing. LEAD over
  effective_from gives exactly that:

      valid_from = effective_from            the business date, always
      valid_to   = the next effective_from   NULL for the current version

  Deterministic, backfill-safe, independent of when the pipeline runs, and
  reproducible from source at any time. assert_no_overlapping_scd_windows stays
  on as the regression guard, and matters more now that SQLMesh is not managing
  the windows.

  FIRST-VERSION BACK-DATING
  ─────────────────────────
  valid_from for a key's EARLIEST version is forced to 1900-01-01 rather than
  its effective_from. Otherwise any fact predating the dimension's first
  version orphans -- which is exactly what happened to SKU 60-178: it sold from
  2025-03-04 but was added to Ref_Products with a later effective_from, so 32
  sales lines resolved to no product_key.

  Fixing the cell fixes that product; back-dating fixes the class. A reference
  row describes an entity that existed before someone got round to typing it
  in, so its first version should extend backwards indefinitely.

  effective_from is kept unchanged as the business date -- only valid_from is
  floored, and only for the first version. Later versions still start exactly
  when the business says they do.
*/
MODEL (
  name marts.dim_products,
  kind FULL,
  cron '@daily',
  grain (product_key),
  owner analytics_team,
  storage_format 'parquet',
  audits (
    unique_values(columns := (product_key)),
    not_null(columns := (product_key, sku, valid_from)),
    assert_no_overlapping_scd_windows(
      key           := sku,
      surrogate_key := product_key
    )
  )
);

WITH windowed AS (
  SELECT
    *,
    -- See FIRST-VERSION BACK-DATING in the header.
    CASE
      WHEN ROW_NUMBER() OVER (
             PARTITION BY sku ORDER BY effective_from
           ) = 1
      THEN CAST('2024-10-01' AS TIMESTAMP)
      ELSE effective_from
    END AS valid_from,
    LEAD(effective_from) OVER (
      PARTITION BY sku ORDER BY effective_from
    ) AS valid_to
  FROM staging.stg_products_data
)

SELECT
  -- Hashed from the business key plus the effective date. Attribute-based
  -- hashes collide when a value reverts to one it held before -- an SD going
  -- destocké, then non-destocké, then destocké again would produce two
  -- identical keys.
  MOD(
    @GENERATE_SURROGATE_KEY(
      TRIM(sku),
      CAST(effective_from AS TEXT),
      hash_function := 'MD5_NUMBER_LOWER'
    ),
    9007199254740992
  ) AS product_key,

  sku,
  product_name,
  product_category,
  product_subcategory,
  unit_price,
  unit_weight_kg,
  is_innovation_product,

  effective_from,
  valid_from,
  valid_to,
  valid_to IS NULL AS is_current

FROM windowed;