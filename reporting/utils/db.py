"""
reporting/utils/db.py
DuckDB connection pool and query executor.
"""
import os
import duckdb
import pandas as pd
import streamlit as st
from pathlib import Path

# Allow override via env var (useful in CI/testing)
from reporting.config import DB_PATH


@st.cache_resource(show_spinner=False)
def get_connection() -> duckdb.DuckDBPyConnection:
    """Return a shared read-only connection to serving.db."""
    db_path = Path(DB_PATH)
    if not db_path.exists():
        st.error(f"⚠️  serving.db not found at `{db_path}`. Run the pipeline first.")
        st.stop()
    return duckdb.connect(str(db_path), read_only=True)


@st.cache_data(ttl=300, show_spinner=False)
def query(_sql: str, params: tuple = ()) -> pd.DataFrame:
    """
    Execute a SQL query and return a DataFrame.
    Results are cached for 5 minutes (ttl=300s).
    Leading underscore on `_sql` prevents Streamlit from hashing it.
    """
    conn = get_connection()
    return conn.execute(_sql, list(params)).df()


def scalar(_sql: str, params: tuple = (), default=None):
    """Return a single scalar value from a query."""
    df = query(_sql, params)
    if df.empty:
        return default
    return df.iloc[0, 0]


def available_years() -> list[int]:
    df = query("SELECT DISTINCT sale_year FROM fact_sales ORDER BY sale_year DESC")
    return df["sale_year"].tolist() if not df.empty else []


def available_months(year: int) -> list[int]:
    df = query(
        "SELECT DISTINCT sale_month FROM fact_sales WHERE sale_year = ? ORDER BY sale_month",
        (year,),
    )
    return df["sale_month"].tolist() if not df.empty else []


def available_regions() -> list[str]:
    df = query("SELECT DISTINCT region FROM dim_salesperson WHERE region IS NOT NULL ORDER BY region")
    return df["region"].tolist() if not df.empty else []


def available_channels() -> list[str]:
    df = query("SELECT DISTINCT sales_channel FROM dim_salesperson WHERE sales_channel IS NOT NULL ORDER BY sales_channel")
    return df["sales_channel"].tolist() if not df.empty else []


def available_categories() -> list[str]:
    df = query("SELECT DISTINCT product_category FROM dim_products WHERE product_category IS NOT NULL ORDER BY product_category")
    return df["product_category"].tolist() if not df.empty else []
