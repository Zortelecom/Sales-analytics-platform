"""
reporting/utils/db.py
DuckDB connection pool and query executor.
All table references use the configured DB_SCHEMA (default: "bi").
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import duckdb
import pandas as pd
import streamlit as st

from reporting.config import DB_PATH, DB_SCHEMA


@st.cache_resource(show_spinner=False)
def get_connection() -> duckdb.DuckDBPyConnection:
    """Return a shared read-only connection to serving.db."""
    db_path = Path(DB_PATH)
    if not db_path.exists():
        st.error(f"⚠️  Database not found at `{db_path}`. Run the pipeline first.")
        st.stop()
    return duckdb.connect(str(db_path), read_only=True)


@st.cache_data(ttl=300, show_spinner=False)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    """
    Execute a SQL query and return a DataFrame.
    Cached for 5 min.
    """
    conn = get_connection()
    return conn.execute(sql, list(params)).df()


def scalar(sql: str, params: tuple = (), default=None):
    """Return a single scalar value."""
    df = query(sql, params)
    if df.empty:
        return default
    return df.iloc[0, 0]


def _first_col(df: pd.DataFrame) -> list:
    """
    Safely extract the first column of a single-column result DataFrame.
    Using positional access (.iloc[:, 0]) avoids KeyError when DuckDB
    returns the column under an unexpected name (e.g. from a view alias).
    """
    if df.empty:
        return []
    return df.iloc[:, 0].dropna().tolist()


# ---------------------------------------------------------------------------
# Schema-aware filter helpers
# ---------------------------------------------------------------------------

def available_years() -> list[int]:
    df = query(
        f"SELECT DISTINCT sale_year FROM {DB_SCHEMA}.fact_sales ORDER BY sale_year DESC"
    )
    return [int(v) for v in _first_col(df)]


def available_months(year: int) -> list[int]:
    df = query(
        f"SELECT DISTINCT sale_month FROM {DB_SCHEMA}.fact_sales "
        f"WHERE sale_year = ? ORDER BY sale_month",
        (year,),
    )
    return [int(v) for v in _first_col(df)]


def available_regions() -> list[str]:
    df = query(
        f"SELECT DISTINCT region FROM {DB_SCHEMA}.dim_salesperson "
        f"WHERE region IS NOT NULL ORDER BY region"
    )
    return [str(v) for v in _first_col(df)]


def available_channels() -> list[str]:
    df = query(
        f"SELECT DISTINCT sales_channel FROM {DB_SCHEMA}.dim_salesperson "
        f"WHERE sales_channel IS NOT NULL ORDER BY sales_channel"
    )
    return [str(v) for v in _first_col(df)]


def available_categories() -> list[str]:
    df = query(
        f"SELECT DISTINCT product_category FROM {DB_SCHEMA}.dim_products "
        f"WHERE product_category IS NOT NULL ORDER BY product_category"
    )
    return [str(v) for v in _first_col(df)]