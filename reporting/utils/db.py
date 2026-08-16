"""
reporting/utils/db.py
DuckDB connection and query executor -- and the single row-level-security
enforcement point for the whole app.

CHANGES vs. the previous version
--------------------------------
0. Source: the DuckLake catalog, attached read-only, instead of a copied
   serving.db. The bi_* views are SQLMesh models living in the lake, so there
   is no copy to go stale and no post-sync view script to drift.

   Object names stay BARE in SQL (`FROM v_sales_base`). Two reasons, and the
   second is the load-bearing one:

     a. SQLMesh namespaces schemas per environment -- bi__dev / marts__dev in
        dev, bi / marts in prod -- so a hardcoded prefix would be wrong in one
        of them. A search path resolves both from one variable.
     b. rls.py keys SCOPE_COLUMNS on bare lowercased names and injects `Via`
        subqueries that reference objects bare. Qualifying every reference in
        queries.py would mean auditing every Via.source as well, and a miss
        there fails OPEN -- a scoped user silently reading unscoped rows.
        Leaving names bare changes nothing rls.py sees.

   DB_SCHEMA is gone: fact_sales lives in marts__dev while the v_* views live
   in bi__dev, so one schema constant cannot address both.

   ⚠ A DuckDB-file catalog admits many readers OR one writer. While
   `sqlmesh plan` runs, this app cannot attach. See docs/LAYER3_SERVING.md
   phase B (PostgreSQL catalog) for the fix.

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

import logging
import os
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

from reporting.auth.rls import RLSError, apply_rls
from shared.env import load_env, resolve_path
from shared.paths import WAREHOUSE_DIR

load_env()
logger = logging.getLogger(__name__)

CATALOG_ALIAS = "sales_lakehouse"

# Logical schemas the app reads, in resolution order. bi first so a bare name
# present in both resolves to the semantic view, not the underlying mart.
SEARCH_SCHEMAS = ("bi", "marts", "meta")


def environment() -> str:
    return os.getenv("SQLMESH_ENV", "dev")


def schema(logical: str = "bi") -> str:
    """Physical schema for a logical one: bi -> bi__dev in dev, bi in prod."""
    env = environment()
    return logical if env == "prod" else f"{logical}__{env}"


def catalog_path() -> Path:
    return resolve_path(os.getenv("DUCKLAKE_CATALOG_PATH"),
                        WAREHOUSE_DIR / "catalog.ducklake")


def _search_path_sql(con: duckdb.DuckDBPyConnection) -> str | None:
    """
    Build SET search_path from the schemas that actually exist.

    SET search_path FAILS OUTRIGHT if any listed schema is absent, so a
    hardcoded list would take the app down whenever one had not been created
    yet. Returns None when none exist -- a clear error from the first query
    beats a confusing one from SET.
    """
    present = {
        r[0] for r in con.execute(
            "SELECT schema_name FROM duckdb_schemas() WHERE database_name = ?",
            [CATALOG_ALIAS],
        ).fetchall()
    }
    wanted = [schema(s) for s in SEARCH_SCHEMAS if schema(s) in present]
    if not wanted:
        return None
    return "SET search_path = '" + ",".join(f"{CATALOG_ALIAS}.{w}" for w in wanted) + "'"


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def _connection() -> tuple[duckdb.DuckDBPyConnection, str]:
    """One read-only handle on the lake, plus the SET statement to replay.

    Returns a TUPLE rather than stashing the SQL on the connection:
    DuckDBPyConnection is a C extension type with no __dict__, so
    `con.__search_path_sql = ...` raises AttributeError. Caching the pair keeps
    the two together without needing anywhere to put it.
    """
    global _SEARCH_PATH_SQL
    path = catalog_path()
    if not path.exists():
        st.error(
            f"⚠️ DuckLake catalog not found at `{path}`. "
            f"Check DUCKLAKE_CATALOG_PATH in .env and run the pipeline first."
        )
        st.stop()

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    try:
        con.execute(f"ATTACH 'ducklake:{path.as_posix()}' AS {CATALOG_ALIAS} (READ_ONLY)")
    except duckdb.Error as exc:
        st.error(
            f"⚠️ Could not attach the lake: {exc}\n\n"
            f"If the pipeline is running, wait for it to finish -- a DuckDB "
            f"file catalog allows many readers or one writer, not both."
        )
        st.stop()
    con.execute(f"USE {CATALOG_ALIAS}")

    sp = _search_path_sql(con)
    if sp is None:
        st.error(
            f"⚠️ None of {[schema(s) for s in SEARCH_SCHEMAS]} exist in the "
            f"catalog. Has `sqlmesh plan {environment()}` run?"
        )
        st.stop()
    con.execute(sp)
    _SEARCH_PATH_SQL = sp                       # <-- changed
    logger.info("Attached %s read-only | env=%s | %s", path, environment(), sp)
    return con


def get_connection() -> duckdb.DuckDBPyConnection:
    """A per-call cursor over the shared connection.

    Kept under the old name so existing imports keep working. Always returns a
    fresh cursor: DuckDB connection objects are not safe to use concurrently,
    cursors are.

    ⚠ search_path does NOT inherit through cursor() -- verified, not assumed.
    Without the replay below, every bare object name fails to resolve. This is
    why callers must go through here rather than through _connection().
    """
    parent = _connection()
    cur = parent.cursor()
    sp = _SEARCH_PATH_SQL                       # <-- changed
    if sp:
        cur.execute(sp)
    return cur


def reset_connection() -> None:
    """Drop the cached handle.

    There is no longer a file swap to recover from, but this is still useful
    after a `sqlmesh plan` adds or renames a schema -- the search path is
    computed once, at attach time.
    """
    global _SEARCH_PATH_SQL                     # <-- changed
    _connection.clear()
    _SEARCH_PATH_SQL = None 


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


def object_exists(name: str, logical: str = "bi") -> bool:
    """Does a view or table of this name exist in the given logical schema?

    Scoped on purpose. queries.py's _view_exists used to hit
    information_schema.tables with NO schema filter, which matched an object of
    that name in ANY schema -- staging, raw, a SQLMesh physical table. Harmless
    against a single-schema serving.db; against the lake it reports a view as
    present that the search path cannot reach, and the fallback never fires.
    """
    df = query(
        "SELECT COUNT(*) AS n FROM ("
        "  SELECT view_name AS nm, schema_name FROM duckdb_views()"
        "  UNION ALL"
        "  SELECT table_name, schema_name FROM duckdb_tables()"
        ") WHERE nm = ? AND schema_name = ?",
        (name, schema(logical)),
        silent=True,
    )
    return (not df.empty) and int(df.iloc[0]["n"]) > 0


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
    df = query("SELECT MAX(sale_date) FROM fact_sales", silent=True)
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
        f"SELECT DISTINCT sale_year FROM fact_sales ORDER BY sale_year DESC"
    )
    return [int(v) for v in _first_col(df)]


def available_months(year: int) -> list[int]:
    df = query(
        f"SELECT DISTINCT sale_month FROM fact_sales "
        f"WHERE sale_year = ? ORDER BY sale_month",
        (year,),
    )
    return [int(v) for v in _first_col(df)]


def available_regions() -> list[str]:
    df = query(
        f"SELECT DISTINCT region FROM dim_salesperson "
        f"WHERE region IS NOT NULL ORDER BY region"
    )
    return [str(v) for v in _first_col(df)]


def available_subregions(regions: list[str] | None = None) -> list[str]:
    sql = (
        f"SELECT DISTINCT subregion FROM dim_salesperson "
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
        f"SELECT DISTINCT sales_channel FROM fact_sales "
        f"WHERE sales_channel IS NOT NULL ORDER BY sales_channel"
    )
    return [str(v) for v in _first_col(df)]


def available_categories() -> list[str]:
    df = query(
        f"SELECT DISTINCT product_category FROM dim_products "
        f"WHERE product_category IS NOT NULL ORDER BY product_category"
    )
    return [str(v) for v in _first_col(df)]