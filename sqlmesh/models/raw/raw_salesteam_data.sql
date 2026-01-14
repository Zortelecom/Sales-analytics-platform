MODEL (
  name raw.salesteam_data,
  kind SEED (
    path '../../seeds/salesteam_data.csv'
  )
  columns (
    fullname TEXT,
    salesperson_id TEXT,
    region TEXT,
    subregion TEXT,
    channel TEXT,
    supervisor TEXT,
    salesteam_ref_id TEXT
  )
);