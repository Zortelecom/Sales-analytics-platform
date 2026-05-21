AUDIT (
    name assert_amount_is_integer_xaf,
    description 'Ensures total_amount is an integer value in XAF currency, which does not use decimals.',
)
SELECT
    sales_line_id,
    sale_date,
    sku,
    total_amount
FROM @this_model
WHERE total_amount != FLOOR(total_amount);