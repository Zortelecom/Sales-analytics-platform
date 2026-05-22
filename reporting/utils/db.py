"""
reporting/utils/db.py
DuckDB connection pool and query executor.
All table references use the configured DB_SCHEMA (default: "bi").
"""
from pathlib import Path
import threading
import duckdb
import pandas as pd
import streamlit as st

from reporting.config import DB_PATH, DB_SCHEMA

_local = threading.local()

def get_connection() -> duckdb.DuckDBPyConnection:
    """Return a shared read-only connection to serving.db."""
    if not hasattr(_local, "conn") or _local.conn is None:
        db_path = Path(DB_PATH)
        if not db_path.exists():
            st.error(f"⚠️  Database not found at `{db_path}`. Run the pipeline first.")
            st.stop()
        _local.conn = duckdb.connect(str(db_path), read_only=True)
    return _local.conn


@st.cache_data(ttl=300, show_spinner=False)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    """Execute a parameterised query and return a DataFrame.
    Errors are caught and surfaced in the UI instead of raw tracebacks.
    """
    try:
        conn = get_connection()
        return conn.execute(sql, list(params)).df()
    except duckdb.Error as e:
        st.error(f"⚠️ Query failed: {e}")
        return pd.DataFrame()


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
# Data freshness indicator (§4.1)
# ---------------------------------------------------------------------------

def data_freshness() -> str | None:
    """Return the MAX(sale_date) from fact_sales as a staleness indicator."""
    df = query(f"SELECT MAX(sale_date) FROM {DB_SCHEMA}.fact_sales")
    if df.empty:
        return None
    val = df.iloc[0, 0]
    return str(val) if val is not None else None


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