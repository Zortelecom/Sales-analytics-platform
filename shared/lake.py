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

CREDENTIALS GO IN A SECRET, NOT IN THE ATTACH STRING
────────────────────────────────────────────────────
DuckLake echoes the Postgres connection string in its exceptions:

    IO Error: Failed to attach DuckLake MetaData "__ducklake_metadata_lake"
    Unable to connect to Postgres at user='lake_writer' password='hunter2'
    host='localhost' dbname='ducklake_catalog'

So an inline password ends up in the Dagster daemon log, in a Streamlit error
banner, and in every stack trace anyone pastes anywhere -- every time the
service is down or a password is wrong, which is exactly when people share
logs. Redacting our own logging does not help; this is DuckDB's own message.

A DuckDB SECRET holds the credential instead, and the ATTACH refers to it by
name, so the exception has nothing to echo.

TEMPORARY, not PERSISTENT. A persistent secret is written to
~/.duckdb/stored_secrets/*.json as a readable file -- a SECOND copy of the
credential to rotate, on a machine where .env already holds one. A temporary
secret lives only in the connection that created it.

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


SECRET_NAME = "sales_lake_pg"


def is_postgres_catalog() -> bool:
    """True when the catalog is PostgreSQL, by either configuration style."""
    return bool(os.getenv("DUCKLAKE_CATALOG_CONN") or os.getenv("PG_CATALOG_HOST"))


def _pg_settings(role: Optional[str]) -> Optional[dict]:
    """
    Postgres connection parameters as discrete values, or None.

    Preferred over DUCKLAKE_CATALOG_CONN because discrete values can go into a
    secret, and a connection string cannot without being parsed -- and parsing
    a Postgres connection string correctly (quoting, escapes, URI vs keyword
    form) is not something to do by hand for a credential.

    Per-role user and password, one shared host/port/database:

        PG_CATALOG_HOST=localhost
        PG_CATALOG_PORT=5432
        PG_CATALOG_DB=sales_lakehouse
        PG_CATALOG_USER=lake_writer            # default for every role
        PG_CATALOG_PASSWORD=...
        PG_CATALOG_USER_READER=bi_reader       # overrides for role="reader"
        PG_CATALOG_PASSWORD_READER=...
    """
    host = os.getenv("PG_CATALOG_HOST")
    if not host:
        return None

    suffix = f"_{role.upper()}" if role else ""
    user = os.getenv(f"PG_CATALOG_USER{suffix}") or os.getenv("PG_CATALOG_USER")
    password = (os.getenv(f"PG_CATALOG_PASSWORD{suffix}")
                or os.getenv("PG_CATALOG_PASSWORD"))

    if not user:
        raise RuntimeError(
            "PG_CATALOG_HOST is set but PG_CATALOG_USER is not. Set a user, or "
            "unset PG_CATALOG_HOST to fall back to the DuckDB file catalog."
        )

    return {
        "host": host,
        "port": os.getenv("PG_CATALOG_PORT", "5432"),
        "database": os.getenv("PG_CATALOG_DB", "sales_lakehouse"),
        "user": user,
        "password": password or "",
    }


def _scrub(message: str) -> str:
    """
    Remove anything that looks like a password from an error message.

    Belt and braces: the secret path should mean no credential reaches an
    exception, but the inline fallback below can still produce one, and an
    error is precisely what gets copied into a bug report.
    """
    import re
    message = re.sub(r"(password\s*=\s*)'[^']*'", r"\1'***'", message)
    message = re.sub(r"(password\s*=\s*)(\S+)", r"\1***", message)
    return re.sub(r"(://[^:/@\s]+:)[^@\s]+@", r"\1***@", message)


def _create_secret(con: duckdb.DuckDBPyConnection, settings: dict) -> bool:
    """
    Create a temporary Postgres secret. False if the build does not support it.

    The CREATE SECRET statement itself carries the password, so it is never
    logged -- only whether it succeeded.
    """
    try:
        con.execute(f"""
            CREATE OR REPLACE TEMPORARY SECRET {SECRET_NAME} (
                TYPE postgres,
                HOST '{settings["host"]}',
                PORT {int(settings["port"])},
                DATABASE '{settings["database"]}',
                USER '{settings["user"]}',
                PASSWORD '{settings["password"].replace("'", "''")}'
            )
        """)
        return True
    except duckdb.Error as exc:
        logger.warning(
            "Could not create a Postgres secret (%s); falling back to an "
            "inline connection string. The password may then appear in "
            "DuckDB error messages.", _scrub(str(exc).splitlines()[0]),
        )
        return False


def data_path() -> Path:
    return resolve_path(os.getenv("PARQUET_PATH"), WAREHOUSE_DIR / "parquet")


def catalog_uri(role: Optional[str] = None, with_secret: bool = False) -> str:
    """
    The `ducklake:...` URI for ATTACH.

    with_secret=True returns `ducklake:postgres:` with no parameters -- the
    credential comes from the secret instead, which is the point.
    """
    if with_secret:
        return "ducklake:postgres:"

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


def describe(role: Optional[str] = None) -> str:
    """A log-safe description of the catalog. Never renders a password."""
    settings = _pg_settings(role)
    if settings:
        return (f"postgres: {settings['user']}@{settings['host']}:"
                f"{settings['port']}/{settings['database']} (via secret)")
    if not is_postgres_catalog():
        return f"duckdb file: {catalog_uri().removeprefix('ducklake:')}"
    return "postgres: " + _scrub(os.getenv("DUCKLAKE_CATALOG_CONN", ""))


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

    settings = _pg_settings(role)
    using_secret = bool(settings) and _create_secret(con, settings)

    uri = catalog_uri(role, with_secret=using_secret)
    options = ["READ_ONLY"] if read_only else []
    if using_secret:
        # META_ parameters are forwarded to the metadata catalog. META_SECRET
        # names the secret explicitly rather than relying on DuckDB matching a
        # default one -- deterministic, and it fails loudly if the secret was
        # not created.
        options.append(f"META_SECRET {SECRET_NAME}")

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
            # Scrubbed: DuckLake echoes the connection string, password and
            # all, in exactly this class of error.
            raise RuntimeError(
                f"Could not attach the lake ({describe(role)}).\n"
                f"  without DATA_PATH: {_scrub(str(first_error).splitlines()[0])}\n"
                f"  with DATA_PATH:    {_scrub(str(second_error).splitlines()[0])}\n"
                f"If the catalog is PostgreSQL, check the service is running "
                f"and the role has CONNECT on the database."
            ) from None

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
