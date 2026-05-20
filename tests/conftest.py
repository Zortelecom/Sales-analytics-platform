# tests/conftest.py
"""Shared pytest fixtures."""

import pytest
import duckdb


@pytest.fixture
def ducklake_catalog(tmp_path):
    """
    Create a minimal DuckDB file that acts as a DuckLake catalog for tests.

    Schema: marts__dev
    Tables: fact_sales (10 rows), dim_products (10 rows)
    """
    warehouse = tmp_path / "data" / "warehouse"
    warehouse.mkdir(parents=True)
    catalog_path = warehouse / "catalog.ducklake"

    conn = duckdb.connect(str(catalog_path))

    conn.execute("CREATE SCHEMA marts__dev")

    conn.execute("""
        CREATE TABLE marts__dev.fact_sales (
            sales_line_id     VARCHAR,
            sale_date         DATE,
            sku               VARCHAR,
            salesperson_id    VARCHAR,
            product_key       VARCHAR,
            total_amount      DECIMAL(10,2),
            quantity          INTEGER,
            unit_price_actual DECIMAL(10,2)
        )
    """)

    conn.execute("""
        CREATE TABLE marts__dev.dim_products (
            product_key       VARCHAR,
            sku               VARCHAR,
            product_name      VARCHAR,
            unit_price        DECIMAL(10,2),
            product_category  VARCHAR
        )
    """)

    for i in range(10):
        conn.execute(f"""
            INSERT INTO marts__dev.fact_sales
            VALUES ('SL{i:02d}', '2024-01-{((i % 9) + 1):02d}',
                    'SKU{i:02d}', 'SP{i:02d}', 'PK{i:02d}',
                    100.00, 1, 100.00)
        """)
        conn.execute(f"""
            INSERT INTO marts__dev.dim_products
            VALUES ('PK{i:02d}', 'SKU{i:02d}', 'Product {i}',
                    100.00, 'Category {i % 3}')
        """)

    conn.close()
    return catalog_path