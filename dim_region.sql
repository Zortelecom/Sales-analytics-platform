/*MODEL (
  name sales_lakehouse.dimensions.dim_geography,
  kind FULL,
  grain (geography_key)
);

WITH unique_geos AS (
  SELECT DISTINCT region, subregion, city
  FROM sales_lakehouse.dimensions.dim_client
  UNION
  SELECT DISTINCT region, subregion, NULL as city
  FROM sales_lakehouse.dimensions.dim_salesperson
  WHERE is_current_version = TRUE
)
SELECT
  MD5(CONCAT(region, '|', subregion, '|', COALESCE(city, '')))::VARCHAR AS geography_key,
  region,
  subregion,
  city,
  CONCAT(region, ' - ', subregion) AS region_subregion
FROM unique_geos;


2. UPDATE dim_client.sql - Add geography_key:

SELECT
  ...,
  -- Add this line:
  g.geography_key,
  ...
FROM sales_lakehouse.seeds.clientSD_data c
-- Add this join:
LEFT JOIN sales_lakehouse.dimensions.dim_geography g
  ON c.region = g.region
  AND c.subregion = g.subregion
  AND c.city = g.city;


3. UPDATE fact_sales.sql - Add geography_key:

SELECT
  ...,
  g.geography_key,  -- Add this
  ...
FROM cleaned_sales s
-- Add this join:
LEFT JOIN sales_lakehouse.dimensions.dim_geography g
  ON s.region = g.region
  AND s.subregion = g.subregion;


4. UPDATE metrics - Add geography_key to GROUP BY:

FROM fact_sales f
LEFT JOIN dim_geography g ON f.geography_key = g.geography_key
GROUP BY ..., g.region, g.subregion;*/