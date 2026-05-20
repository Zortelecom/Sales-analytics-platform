# tests/assets/test_data_quality.py
"""Tests for the five audit functions in data_quality.py.

Each test:
  1. Spins up an in-memory DuckDB instance.
  2. Creates the marts__dev / staging__dev schemas and fixture tables.
  3. Injects rows with known defects.
  4. Calls the audit function.
  5. Asserts failing_rows length and trace content.
"""

import duckdb
import pytest
from unittest.mock import patch

from orchestration.assets.data_quality import (
    _audit_not_null_fact_sales,
    _audit_negative_amount,
    _audit_orphaned_products,
    _audit_amount_vs_qty_price,
    _audit_product_price,
)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    conn = duckdb.connect(":memory:")

    # Schemas must exist before creating tables
    conn.execute("CREATE SCHEMA marts__dev")
    conn.execute("CREATE SCHEMA staging__dev")

    # fact_sales — mirrors the columns referenced by the audit queries
    conn.execute("""
        CREATE TABLE marts__dev.fact_sales (
            sales_line_id   VARCHAR,
            sale_date       DATE,
            sku             VARCHAR,
            salesperson_id  VARCHAR,
            product_key     VARCHAR,
            total_amount    DECIMAL(10,2),
            quantity        INTEGER,
            unit_price_actual DECIMAL(10,2)
        )
    """)

    # stg_sales_data — used for tracing back to source Excel files
    conn.execute("""
        CREATE TABLE staging__dev.stg_sales_data (
            sales_line_id       VARCHAR,
            sale_date           DATE,
            sku                 VARCHAR,
            salesperson_id      VARCHAR,
            filename_subregion  VARCHAR
        )
    """)

    # stg_products_data — used by the product-price audit
    conn.execute("""
        CREATE TABLE staging__dev.stg_products_data (
            product_key   VARCHAR,
            sku           VARCHAR,
            product_name  VARCHAR,
            unit_price    DECIMAL(10,2)
        )
    """)

    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# _audit_not_null_fact_sales
# ---------------------------------------------------------------------------

def test_audit_not_null_fact_sales(db):
    # Good row
    db.execute("""
        INSERT INTO marts__dev.fact_sales
        VALUES ('1', '2024-01-01', 'SKU1', 'SP1', 'PK1', 100.00, 1, 100.00)
    """)
    # Bad row: sku is NULL
    db.execute("""
        INSERT INTO marts__dev.fact_sales
        VALUES ('2', '2024-01-01', NULL, 'SP2', 'PK2', 100.00, 1, 100.00)
    """)
    # Trace row in staging
    db.execute("""
        INSERT INTO staging__dev.stg_sales_data
        VALUES ('2', '2024-01-01', NULL, 'SP2', 'ExSD-Sales-Yde.xlsx')
    """)

    failing, trace = _audit_not_null_fact_sales(db)

    assert len(failing) == 1
    assert failing[0]["sku"] is None

    assert len(trace) == 1
    assert trace[0]["source_file"] == "ExSD-Sales-Yde.xlsx"
    assert trace[0]["null_column"] == "sku"


# ---------------------------------------------------------------------------
# _audit_negative_amount
# ---------------------------------------------------------------------------

def test_audit_negative_amount(db):
    db.execute("""
        INSERT INTO marts__dev.fact_sales
        VALUES ('1', '2024-01-01', 'SKU1', 'SP1', 'PK1', -50.00, 1, 50.00)
    """)
    db.execute("""
        INSERT INTO staging__dev.stg_sales_data
        VALUES ('1', '2024-01-01', 'SKU1', 'SP1', 'ExSD-Sales-Est.xlsx')
    """)

    failing, trace = _audit_negative_amount(db)

    assert len(failing) == 1
    assert failing[0]["total_amount"] == -50.00

    assert len(trace) == 1
    assert trace[0]["source_file"] == "ExSD-Sales-Est.xlsx"


# ---------------------------------------------------------------------------
# _audit_orphaned_products
# ---------------------------------------------------------------------------

def test_audit_orphaned_products(db):
    db.execute("""
        INSERT INTO marts__dev.fact_sales
        VALUES ('1', '2024-01-01', 'SKU-ORPHAN', 'SP1', NULL, 100.00, 1, 100.00)
    """)
    db.execute("""
        INSERT INTO staging__dev.stg_sales_data
        VALUES ('1', '2024-01-01', 'SKU-ORPHAN', 'SP1', 'ExSD-Sales-Dla.xlsx')
    """)

    failing, trace = _audit_orphaned_products(db)

    assert len(failing) == 1
    assert failing[0]["product_key"] is None

    assert len(trace) == 1
    assert trace[0]["source_file"] == "ExSD-Sales-Dla.xlsx"
    assert "absent from products reference" in trace[0]["reason"]


# ---------------------------------------------------------------------------
# _audit_amount_vs_qty_price
# ---------------------------------------------------------------------------

def test_audit_amount_vs_qty_price(db):
    # quantity=10, unit_price=10.00 → expected 100.00, actual 150.00 → 50 % deviation
    db.execute("""
        INSERT INTO marts__dev.fact_sales
        VALUES ('1', '2024-01-01', 'SKU1', 'SP1', 'PK1', 150.00, 10, 10.00)
    """)
    db.execute("""
        INSERT INTO staging__dev.stg_sales_data
        VALUES ('1', '2024-01-01', 'SKU1', 'SP1', 'ExSD-Sales-Yde.xlsx')
    """)

    failing, trace = _audit_amount_vs_qty_price(db)

    assert len(failing) == 1
    assert failing[0]["deviation_pct"] == 50.00

    assert len(trace) == 1
    assert trace[0]["source_file"] == "ExSD-Sales-Yde.xlsx"


# ---------------------------------------------------------------------------
# _audit_product_price
# ---------------------------------------------------------------------------

def test_audit_product_price(db):
    db.execute("""
        INSERT INTO staging__dev.stg_products_data
        VALUES
            ('PK1', 'SKU1', 'Product A', NULL),      -- NULL price
            ('PK2', 'SKU2', 'Product B', 0.50)       -- below threshold
    """)

    with patch(
        "orchestration.assets.data_quality._load_seed_metadata"
    ) as mock_meta:
        mock_meta.return_value = {"Source Files": "References.xlsx"}
        failing, trace = _audit_product_price(db)

    assert len(failing) == 2
    assert trace[0]["source_file"] == "References.xlsx"
    assert trace[1]["source_file"] == "References.xlsx"