MODEL (
  name raw.sales_data,
  kind SEED (
    path '../../seeds/sales_data.csv'
  ),
  columns (
    salesperson_id TEXT,
    clientsd_id TEXT,
    sd_destocke TEXT,
    date DATE,
    sku TEXT,
    product_name TEXT,
    unit_price TEXT,
    qty TEXT,
    amount TEXT,
    subregion TEXT,
    salesperson TEXT,
    supervisor TEXT,
    channel TEXT,
    product_cat TEXT,
    product_subcat TEXT,
    unit_weight TEXT,
    is_innovation TEXT,
    sales_line_id TEXT,
    filename_subregion TEXT
  )
);