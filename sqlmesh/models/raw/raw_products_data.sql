MODEL (
  name raw.products_data,
  kind SEED (
    path '../../seeds/products_data.csv'
  ),
  columns (
    sku TEXT,
    product_name TEXT,
    product_category TEXT,
    product_subcategory TEXT,
    unit_price TEXT,
    unit_weight TEXT,
    is_innovation TEXT,
    product_ref_id TEXT
  )
);