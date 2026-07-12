MODEL (
  name marts.rep_promo_impact_interim,
  kind FULL,
  start '2026-04-01'
);

-- Short-term answer to "impact des promos sur les ventes", usable now,
-- while the generic plan/tier engine is being built.
--
-- Baseline = the calendar month immediately preceding the promo period.
-- Simplest defensible baseline available without a full YoY history yet;
-- swap for a trailing-N-week average or YoY once more periods are loaded.
--
-- KNOWN GRAIN LIMITATION (flagging rather than papering over):
-- The DG bouclage is recorded at Vendeur x Client-final grain, but the
-- end-retail-client ("ROYAL", "PALACE"...) does not appear to be a tracked
-- dimension in fact_sales — destockage there is recorded at
-- salesperson/SD-purchased-from grain, not down to the vendeur's own
-- resale client. So DG lift below is measured at VENDEUR grain (rolling
-- up all of that vendeur's gains), not at the individual end-client who
-- actually received the gain. Confirm with Evrad whether fact_sales has a
-- finer grain available before trusting DG lift numbers at face value.
--
-- Gains are reported in physical units (seaux/cartons), not valued in CA
-- terms — that needs a unit cost per reward product, not yet wired in.

WITH sd_participants AS (
    SELECT
        g.period_label,
        g.kp_name,
        g.client_key AS sd_name_raw,
        cs.clientsd_key AS sd_key,
        SUM(g.gain_qty) AS total_gain_qty,
        COUNT(DISTINCT g.product_label) AS distinct_products_gained
    FROM marts.fact_promo_gains_actual AS g
    LEFT JOIN marts.dim_clientsd AS cs
        ON LOWER(TRIM(g.client_key)) = LOWER(TRIM(cs.client_name))
        AND cs.valid_to IS NULL
    WHERE g.channel_type = 'SD'
    GROUP BY 1, 2, 3, 4
),

sd_impact AS (
    SELECT
        sp.period_label,
        'SD' AS channel_type,
        'SD' AS entity_type,
        sp.sd_name_raw AS entity_name,
        sp.kp_name,
        sp.total_gain_qty,
        sp.distinct_products_gained,
        SUM(CASE
            WHEN f.transaction_date >= CAST(sp.period_label || '-01' AS DATE)
             AND f.transaction_date < CAST(sp.period_label || '-01' AS DATE) + INTERVAL 1 MONTH
            THEN f.total_amount ELSE 0
        END) AS ca_promo_period,
        SUM(CASE
            WHEN f.transaction_date >= CAST(sp.period_label || '-01' AS DATE) - INTERVAL 1 MONTH
             AND f.transaction_date < CAST(sp.period_label || '-01' AS DATE)
            THEN f.total_amount ELSE 0
        END) AS ca_baseline_period
    FROM sd_participants AS sp
    LEFT JOIN marts.fact_kp_sd AS f
        ON f.sd_key = sp.sd_key
        AND f.transaction_date >= CAST(sp.period_label || '-01' AS DATE) - INTERVAL 1 MONTH
        AND f.transaction_date < CAST(sp.period_label || '-01' AS DATE) + INTERVAL 1 MONTH
    GROUP BY 1, 2, 3, 4, 5, 6, 7
),

dg_participants AS (
    SELECT
        g.period_label,
        g.kp_name,
        g.vendeur AS vendeur_name_raw,
        sp.salesperson_key,
        SUM(g.gain_qty) AS total_gain_qty,
        COUNT(DISTINCT g.client_key) AS distinct_end_clients_gained,
        COUNT(DISTINCT g.product_label) AS distinct_products_gained
    FROM marts.fact_promo_gains_actual AS g
    LEFT JOIN marts.dim_salesperson AS sp
        ON LOWER(TRIM(g.vendeur)) = LOWER(TRIM(sp.salesperson_name))
        AND sp.valid_to IS NULL
    WHERE g.channel_type = 'DG'
      AND g.vendeur IS NOT NULL
    GROUP BY 1, 2, 3, 4
),

dg_impact AS (
    SELECT
        dp.period_label,
        'DG' AS channel_type,
        'VENDEUR' AS entity_type,
        dp.vendeur_name_raw AS entity_name,
        dp.kp_name,
        dp.total_gain_qty,
        dp.distinct_products_gained,
        SUM(CASE
            WHEN f.sale_date >= CAST(dp.period_label || '-01' AS DATE)
             AND f.sale_date < CAST(dp.period_label || '-01' AS DATE) + INTERVAL 1 MONTH
            THEN f.total_amount ELSE 0
        END) AS ca_promo_period,
        SUM(CASE
            WHEN f.sale_date >= CAST(dp.period_label || '-01' AS DATE) - INTERVAL 1 MONTH
             AND f.sale_date < CAST(dp.period_label || '-01' AS DATE)
            THEN f.total_amount ELSE 0
        END) AS ca_baseline_period
    FROM dg_participants AS dp
    LEFT JOIN marts.fact_sales AS f
        ON f.salesperson_key = dp.salesperson_key
        AND f.sale_date >= CAST(dp.period_label || '-01' AS DATE) - INTERVAL 1 MONTH
        AND f.sale_date < CAST(dp.period_label || '-01' AS DATE) + INTERVAL 1 MONTH
    GROUP BY 1, 2, 3, 4, 5, 6, 7
)

SELECT
    *,
    ca_promo_period - ca_baseline_period AS lift_abs,
    CASE WHEN ca_baseline_period > 0
         THEN (ca_promo_period - ca_baseline_period) / ca_baseline_period
         ELSE NULL END AS lift_pct
FROM sd_impact
UNION ALL
SELECT
    *,
    ca_promo_period - ca_baseline_period AS lift_abs,
    CASE WHEN ca_baseline_period > 0
         THEN (ca_promo_period - ca_baseline_period) / ca_baseline_period
         ELSE NULL END AS lift_pct
FROM dg_impact
