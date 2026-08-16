"""
One place that knows how to attach the lake.

WHY THIS EXISTS
───────────────
The ATTACH statement lived in four modules -- ingestion/load/landing_writer.py,
orchestration/resources/duckdb_resource.py, serving/publish.py and
reporting/utils/db.py. They had already drifted once: one passed DATA_PATH
unconditionally, one only on creation, and one omitted the `ducklake:` prefix
entirely, which makes DuckDB open the catalog as an ordinary database file so
every schema looks EMPTY rather than raising.

With a PostgreSQL catalog the string gains a host, a database, a user and a
password. Four copies of that is four places to leak a credential and four
places to get the DATA_PATH rule wrong.

TWO BACKENDS, ONE INTERFACE
───────────────────────────
    DUCKLAKE_CATALOG_CONN   set   -> PostgreSQL catalog (multi-client)
    DUCKLAKE_CATALOG_CONN   unset -> DuckDB file catalog (single writer)

Switching is one environment variable. Nothing else in the platform changes.

THE DATA_PATH RULE, WHICH DIFFERS BY BACKEND
────────────────────────────────────────────
A DuckDB catalog can derive a default data path from its own file location and
stores it on creation; passing it again on re-attach is redundant and can
conflict. A PostgreSQL catalog has no file to derive from and REQUIRES an
explicit DATA_PATH, at least on the attach that creates it.

Rather than encode a rule that is wrong on one of the two, attach() tries
without DATA_PATH and retries with it -- so a fresh catalog gets one and an
existing catalog keeps the one it already stored.
"""

import logging
import os
from pathlib import Path
from typing import Optional

import duckdb

from shared.env import load_env, resolve_path
from shared.paths import WAREHOUSE_DIR

load_env()
logger = logging.getLogger(__name__)

CATALOG_ALIAS = "sales_lakehouse"


def is_postgres_catalog() -> bool:
    return bool(os.getenv("DUCKLAKE_CATALOG_CONN"))


def data_path() -> Path:
    return resolve_path(os.getenv("PARQUET_PATH"), WAREHOUSE_DIR / "parquet")


def catalog_uri(role: Optional[str] = None) -> str:
    """
    The `ducklake:...` URI for ATTACH.

    `role` selects a per-role connection string when one is set, so ingestion
    can connect as a writer while reporting connects as a reader:

        DUCKLAKE_CATALOG_CONN_READER=postgresql://bi_reader:...@localhost/...

    Falling back to DUCKLAKE_CATALOG_CONN means least privilege is opt-in --
    the platform works with one credential and gets safer with four, rather
    than refusing to start until all four exist.
    """
    if role:
        specific = os.getenv(f"DUCKLAKE_CATALOG_CONN_{role.upper()}")
        if specific:
            return f"ducklake:{specific}"

    conn = os.getenv("DUCKLAKE_CATALOG_CONN")
    if conn:
        return f"ducklake:{conn}"

    path = resolve_path(os.getenv("DUCKLAKE_CATALOG_PATH"),
                        WAREHOUSE_DIR / "catalog.ducklake")
    return f"ducklake:{path.as_posix()}"


def describe() -> str:
    """A log-safe description of the catalog. Never renders a password."""
    if not is_postgres_catalog():
        return f"duckdb file: {catalog_uri().removeprefix('ducklake:')}"
    raw = os.getenv("DUCKLAKE_CATALOG_CONN", "")
    parts = [p for p in raw.replace("postgres:", "").split()
             if not p.lower().startswith("password")]
    return "postgres: " + " ".join(parts)


def attach(
    con: duckdb.DuckDBPyConnection,
    alias: str = CATALOG_ALIAS,
    read_only: bool = True,
    role: Optional[str] = None,
    use: bool = True,
) -> duckdb.DuckDBPyConnection:
    """
    Attach the lake to an existing DuckDB connection.

    Loads `postgres` as well as `ducklake` when the catalog is PostgreSQL --
    DuckLake reaches the metadata database through the postgres extension, and
    without it the attach fails with a connection error that reads as though
    the server is down.
    """
    con.execute("INSTALL ducklake; LOAD ducklake;")
    if is_postgres_catalog():
        con.execute("INSTALL postgres; LOAD postgres;")

    uri = catalog_uri(role)
    options = ["READ_ONLY"] if read_only else []

    def _attach(with_data_path: bool) -> None:
        opts = list(options)
        if with_data_path:
            opts.append(f"DATA_PATH '{data_path().as_posix()}/'")
        suffix = f" ({', '.join(opts)})" if opts else ""
        con.execute(f"ATTACH IF NOT EXISTS '{uri}' AS {alias}{suffix}")

    try:
        # An existing catalog already stores its DATA_PATH; passing it again is
        # redundant at best and a conflict at worst.
        _attach(with_data_path=False)
    except duckdb.Error as first_error:
        # A fresh PostgreSQL catalog has no stored path and no file to derive
        # one from, so it must be told. Retry rather than branch on backend:
        # the retry is correct for both, a branch would be wrong for one.
        logger.debug("Attach without DATA_PATH failed (%s); retrying with it",
                     str(first_error).splitlines()[0])
        try:
            _attach(with_data_path=True)
        except duckdb.Error as second_error:
            raise RuntimeError(
                f"Could not attach the lake ({describe()}).\n"
                f"  without DATA_PATH: {str(first_error).splitlines()[0]}\n"
                f"  with DATA_PATH:    {str(second_error).splitlines()[0]}\n"
                f"If the catalog is PostgreSQL, check the server is running "
                f"and the role has CONNECT on the database."
            ) from second_error

    if use:
        con.execute(f"USE {alias}")
    return con


def connect(
    read_only: bool = True,
    role: Optional[str] = None,
    alias: str = CATALOG_ALIAS,
) -> duckdb.DuckDBPyConnection:
    """A new in-memory DuckDB connection with the lake attached."""
    con = duckdb.connect()
    try:
        return attach(con, alias=alias, read_only=read_only, role=role)
    except Exception:
        con.close()
        raise
