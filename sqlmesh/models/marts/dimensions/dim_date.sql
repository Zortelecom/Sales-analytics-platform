MODEL (
  name marts.dim_date,
  kind FULL,
  cron '@monthly',
  grain (date_key),
  owner analytics_team,
  storage_format 'parquet',
  description 'Date dimension with calendar attributes'
);

-- ============================================================
-- FISCAL YEAR CONFIG
-- Adjust @fiscal_year_start_month to match your fiscal calendar.
-- Example: 7 → fiscal year starts in July (FY2025 = Jul 2024–Jun 2025)
--          1 → fiscal year mirrors calendar year (default below)
-- ============================================================

WITH date_spine AS (
  SELECT UNNEST(
    generate_series(
      DATE '2024-10-01',
      DATE '2035-12-31',
      INTERVAL '1 day'
    )
  ) AS date_value
),

base AS (
  SELECT
    date_value,

    -- Core extracts (reused below)
    EXTRACT('year'    FROM date_value)::INTEGER AS _year,
    EXTRACT('month'   FROM date_value)::INTEGER AS _month,
    EXTRACT('day'     FROM date_value)::INTEGER AS _day_of_month,
    EXTRACT('quarter' FROM date_value)::INTEGER AS _quarter,
    EXTRACT('week'    FROM date_value)::INTEGER AS _week_of_year,
    EXTRACT('dow'     FROM date_value)::INTEGER AS _dow  -- 0=Sun … 6=Sat

  FROM date_spine
)

SELECT
  -- ── Surrogate & natural keys ────────────────────────────────
  CAST(STRFTIME(date_value, '%Y%m%d') AS INTEGER)  AS date_key,
  date_value                                        AS date_actual,   -- ← added (used by v_quarterly_kpi join)

  -- ── Calendar hierarchy ──────────────────────────────────────
  _year                                             AS year,
  _quarter                                          AS quarter,
  _month                                            AS month,
  _day_of_month                                     AS day_of_month,
  _week_of_year                                     AS week_of_year,

  -- Week within the month (1–5)
  -- Logic: which occurrence of that weekday in this month?
  -- Simple approach: CEIL(day_of_month / 7.0)
  CEIL(_day_of_month / 7.0)::INTEGER               AS week_of_month,  -- ← added (WoW analysis)

  -- Day of week: 1=Mon … 7=Sun  (ISO-style, avoids 0-based confusion)
  CASE _dow
    WHEN 0 THEN 7   -- Sun → 7
    ELSE _dow
  END                                               AS day_of_week,

  -- ── Fiscal calendar ─────────────────────────────────────────
  -- DEFAULT: fiscal year = calendar year (start month = 1 = January).
  -- To shift: change the two occurrences of `1` below to your start month.
  -- Example for July start (month 7):
  --   fiscal_year  = CASE WHEN _month >= 7 THEN _year ELSE _year - 1 END
  --   fiscal_month = (((_month - 7 + 12) % 12) + 1)
  CASE
    WHEN _month >= 10 THEN _year          -- calendar-aligned fiscal year
    ELSE _year - 1
  END                                               AS fiscal_year,    -- ← added

  ((_month - 10 + 12) % 12 + 1)::INTEGER            AS fiscal_month,   -- ← added (=calendar month when start=Jan)

  -- Fiscal quarter (derives from fiscal_month)
  CEIL(
    ((_month - 10 + 12) % 12 + 1) / 3.0
  )::INTEGER                                        AS fiscal_quarter,  -- ← added (bonus)

  -- ── Display strings ─────────────────────────────────────────
  STRFTIME(date_value, '%Y')                                          AS year_name,
  CONCAT(STRFTIME(date_value, '%Y'), '-Q', _quarter)                  AS quarter_name,
  STRFTIME(date_value, '%Y-%m')                                       AS year_month,
  STRFTIME(date_value, '%B %Y')                                       AS month_name,
  STRFTIME(date_value, '%A')                                          AS day_name,
  CONCAT('W', LPAD(_week_of_year::VARCHAR, 2, '0'), ' ', _year)       AS week_label,  -- ← added (e.g. "W03 2025")

  -- ── Period start dates ──────────────────────────────────────
  DATE_TRUNC('month',   date_value)                AS month_start_date,
  DATE_TRUNC('quarter', date_value)                AS quarter_start_date,
  DATE_TRUNC('year',    date_value)                AS year_start_date,
  DATE_TRUNC('week',    date_value)                AS week_start_date,  -- ← added (Monday of the week)

  -- ── Boolean flags ───────────────────────────────────────────
  CASE WHEN _dow IN (0, 6)           THEN TRUE ELSE FALSE END AS is_weekend,
  CASE WHEN _day_of_month = 1        THEN TRUE ELSE FALSE END AS is_month_start,
  CASE WHEN date_value = LAST_DAY(date_value) THEN TRUE ELSE FALSE END AS is_month_end,

  -- Is this date in the current month / quarter / year at run time?
  CASE WHEN DATE_TRUNC('month',   date_value) = DATE_TRUNC('month',   CURRENT_DATE) THEN TRUE ELSE FALSE END AS is_current_month,
  CASE WHEN DATE_TRUNC('quarter', date_value) = DATE_TRUNC('quarter', CURRENT_DATE) THEN TRUE ELSE FALSE END AS is_current_quarter,
  CASE WHEN _year = EXTRACT('year' FROM CURRENT_DATE) THEN TRUE ELSE FALSE END AS is_current_year

FROM base;