AUDIT (
    name assert_sales_data_is_fresh,
    description 'Ensures the most recent sale in the fact_sales model is within the last 7 days.'
)
SELECT 1 
WHERE 
    (SELECT MAX(sale_date) FROM @this) < CURRENT_DATE - INTERVAL 7 DAY