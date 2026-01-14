MODEL (
  name raw.clientSD_data,
  kind SEED (
    path '../../seeds/clientSD_data.csv'
  ),
  columns (
    sd_name TEXT,
    sd_id TEXT,
    region TEXT,
    kp TEXT,
    subregion TEXT,
    phone TEXT,
    city TEXT,
    is_destocked TEXT,
    client_sd_ref_id TEXT
  )
);
