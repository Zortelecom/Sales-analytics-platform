"""
reporting/utils/db.py
DuckDB connection and query executor -- and the single row-level-security
enforcement point for the whole app.

CHANGES vs. the previous version
--------------------------------
1. Connection: `threading.local()` replaced with `@st.cache_resource`.
   Streamlit runs each session's script in a worker thread drawn from a pool,
   and those threads are recycled, so a thread-local connection was neither
   one-per-session nor one-per-app -- it silently opened a new DuckDB handle
   every time the pool grew and kept them all open. A single cached connection
   plus `conn.cursor()` per query is the documented pattern: cursors are
   independent and safe to use from different threads.

2. RLS: every query is rewritten through `reporting.auth.rls.apply_rls()`
   before execution. Because this is the only place SQL is executed, no page
   -- including the LLM-driven "Ask Your Data" page -- can bypass it.

3. Cache partitioning: `@st.cache_data` is process-wide, shared across all
   users. The cache key now includes the caller's scope fingerprint, so two
   users with different visibility can never collide on a cache entry. Note
   the parameter is named `scope_key`, NOT `_scope_key` -- Streamlit *excludes*
   leading-underscore parameters from the hash, which would have reintroduced
   exactly the leak this prevents.

4. Errors are surfaced outside the cached function, so a failure re-renders on
   every rerun instead of being swallowed by a cache hit.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

from reporting.auth.rls import RLSError, apply_rls
from reporting.config import DB_PATH, DB_SCHEMA


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def _connection() -> duckdb.DuckDBPyConnection:
    """One read-only DuckDB handle for the whole app process."""
    db_path = Path(DB_PATH)
    if not db_path.exists():
        st.error(f"⚠️ Database not found at `{db_path}`. Run the pipeline first.")
        st.stop()
    return duckdb.connect(str(db_path), read_only=True)


def get_connection() -> duckdb.DuckDBPyConnection:
    """A per-call cursor over the shared connection.

    Kept under the old name so existing imports keep working. Always returns a
    fresh cursor: DuckDB connection objects are not safe to use concurrently,
    cursors are.
    """
    return _connection().cursor()


def reset_connection() -> None:
    """Drop the cached handle -- call after a serving-layer file swap."""
    _connection.clear()


# ---------------------------------------------------------------------------
# Current user / scope
# ---------------------------------------------------------------------------

def _principal():
    """Return the logged-in Principal, or None when auth is not in play.

    Imported lazily to keep `db` importable from scripts (introspect, tests)
    that have no Streamlit session.
    """
    try:
        from reporting.auth.session import current_user
        return current_user()
    except Exception:
        return None


def _scope_fingerprint(principal) -> str:
    return principal.cache_key() if principal is not None else "anonymous"


# ---------------------------------------------------------------------------
# Query execution
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def _run(sql: str, params: tuple, scope_key: str) -> pd.DataFrame:
    """Execute pre-scoped SQL. `scope_key` partitions the cache per user scope.

    Raises on failure -- the wrapper decides how to surface it, so errors are
    not cached and therefore re-render on every rerun.
    """
    return get_connection().execute(sql, list(params)).df()


def query(sql: str, params: tuple = (), silent: bool = False) -> pd.DataFrame:
    """Execute a parameterised query under the current user's row-level scope."""
    principal = _principal()
    try:
        scoped_sql = apply_rls(sql, principal)
    except RLSError as exc:
        # A configuration gap, not a user error -- make it loud and specific.
        if not silent:
            st.error(f"🔒 Blocked by row-level security: {exc}")
        return pd.DataFrame()

    try:
        return _run(scoped_sql, tuple(params), _scope_fingerprint(principal))
    except duckdb.Error as exc:
        if not silent:
            st.error(f"⚠️ Query failed: {exc}")
        return pd.DataFrame()


def scalar(sql: str, params: tuple = (), default=None):
    """Return a single scalar value."""
    df = query(sql, params)
    if df.empty:
        return default
    return df.iloc[0, 0]


def _first_col(df: pd.DataFrame) -> list:
    """Safely extract the first column of a single-column result.

    Positional access avoids KeyError when DuckDB returns the column under an
    unexpected name (e.g. from a view alias).
    """
    if df.empty:
        return []
    return df.iloc[:, 0].dropna().tolist()


# ---------------------------------------------------------------------------
# Data freshness indicator
# ---------------------------------------------------------------------------

def data_freshness() -> str | None:
    """MAX(sale_date) from fact_sales, as a staleness indicator."""
    df = query(f"SELECT MAX(sale_date) FROM {DB_SCHEMA}.fact_sales", silent=True)
    if df.empty:
        return None
    val = df.iloc[0, 0]
    return str(val) if val is not None else None


# ---------------------------------------------------------------------------
# Schema-aware filter helpers
#
# These all go through query(), so the option lists a user sees are already
# narrowed to their scope -- they cannot select a region they may not read.
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


def available_subregions(regions: list[str] | None = None) -> list[str]:
    sql = (
        f"SELECT DISTINCT subregion FROM {DB_SCHEMA}.dim_salesperson "
        f"WHERE subregion IS NOT NULL"
    )
    params: list = []
    if regions:
        placeholders = ",".join(["?"] * len(regions))
        sql += f" AND region IN ({placeholders})"
        params.extend(regions)
    sql += " ORDER BY subregion"
    return [str(v) for v in _first_col(query(sql, tuple(params)))]


def available_channels() -> list[str]:
    df = query(
        f"SELECT DISTINCT sales_channel FROM {DB_SCHEMA}.fact_sales "
        f"WHERE sales_channel IS NOT NULL ORDER BY sales_channel"
    )
    return [str(v) for v in _first_col(df)]


def available_categories() -> list[str]:
    df = query(
        f"SELECT DISTINCT product_category FROM {DB_SCHEMA}.dim_products "
        f"WHERE product_category IS NOT NULL ORDER BY product_category"
    )
    return [str(v) for v in _first_col(df)]
    return [str(v) for v in _first_col(df)]