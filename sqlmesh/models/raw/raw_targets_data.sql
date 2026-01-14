MODEL (
  name raw.targets_data,
  kind SEED (
    path '../../seeds/targets_data.csv'
  )
  columns (
    month_year TEXT,
    salesperson_id TEXT,
    product_category TEXT,
    target_amount TEXT,
    target_line_id TEXT
  )
);