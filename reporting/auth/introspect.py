"""
reporting/auth/introspect.py

Audit `reporting/auth/rls.py`'s SCOPE_COLUMNS map against the real lake.

    python -m reporting.auth.introspect
    python -m reporting.auth.introspect --env prod
    python -m reporting.auth.introspect --catalog path/to/catalog.ducklake

(2026-08) Reads the DuckLake catalog instead of serving.db, and scopes the
scan to the schemas the app can actually reach -- bi__<env>, marts__<env>,
meta__<env>. The previous version scanned every schema except
information_schema and pg_catalog, which against the lake would drag in
landing, raw, staging and every SQLMesh physical table, and report each as
NOT MAPPED. Those are not reachable from the app and are not RLS's problem.

For every view and table it prints which scope dimensions can be enforced,
which are declared but reference a column that does not exist (a latent
crash), and which objects are in neither SCOPE_COLUMNS nor UNSCOPED_OBJECTS
(a latent RLSError for scoped users).

Run this whenever you change a model under sqlmesh/models/bi/. That is now the
only place the semantic layer is defined -- bi_views.sql is gone.

It matters more than it used to: the bi views are rebuilt by `sqlmesh plan`,
so a column an RLS mapping depends on can disappear without anyone editing
reporting/ at all. A missing scope column is not a cosmetic break -- for a
scoped user it is either a crash or, if the dimension silently stops being
enforceable, rows they should not see.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import duckdb  # noqa: E402

from reporting.auth.rls import (  # noqa: E402
    INTENTIONALLY_UNMAPPED,
    SCOPE_COLUMNS,
    UNSCOPED_OBJECTS,
    Via,
)
from reporting.utils.db import (  # noqa: E402
    CATALOG_ALIAS,
    SEARCH_SCHEMAS,
    catalog_path,
    schema,
)

# Dimensions we would like every revenue-bearing object to support.
WANTED = ("regions", "subregions", "salespersons", "supervisors", "channels", "clients")

GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"
if not sys.stdout.isatty() or os.name == "nt":
    GREEN = YELLOW = RED = DIM = RESET = ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default=None,
                    help="DuckLake catalog path (default: DUCKLAKE_CATALOG_PATH)")
    ap.add_argument("--env", default=None,
                    help="SQLMesh environment (default: SQLMESH_ENV, or dev)")
    args = ap.parse_args()

    if args.env:
        os.environ["SQLMESH_ENV"] = args.env

    path = Path(args.catalog) if args.catalog else catalog_path()
    if not path.exists():
        print(f"{RED}DuckLake catalog not found: {path}{RESET}")
        return 1

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(f"ATTACH 'ducklake:{path.as_posix()}' AS {CATALOG_ALIAS} (READ_ONLY)")
    con.execute(f"USE {CATALOG_ALIAS}")

    # Only the schemas the app's search path can reach. Scanning the whole
    # catalog would report landing/raw/staging as NOT MAPPED, which is noise:
    # no query in the app can reach them.
    reachable = [schema(s) for s in SEARCH_SCHEMAS]
    placeholders = ",".join(["?"] * len(reachable))
    rows = con.execute(
        f"""
        SELECT schema_name, table_name, column_name
        FROM duckdb_columns()
        WHERE database_name = ? AND schema_name IN ({placeholders})
        ORDER BY schema_name, table_name, column_index
        """,
        [CATALOG_ALIAS, *reachable],
    ).fetchall()

    if not rows:
        print(f"{RED}No objects found in {reachable}.{RESET} "
              f"Has `sqlmesh plan` run for this environment?")
        return 1
    print(f"{DIM}catalog: {path}\nschemas: {', '.join(reachable)}{RESET}")

    objects: dict[str, set[str]] = {}
    schema_of: dict[str, str] = {}
    # NOT `for schema, ...`: that binds a local named `schema` for the whole
    # function, shadowing the imported schema() helper used above and raising
    # UnboundLocalError before this loop is ever reached.
    for schema_name, table, col in rows:
        objects.setdefault(table.lower(), set()).add(col.lower())
        schema_of[table.lower()] = schema_name

    problems = 0

    for name in sorted(objects):
        cols = objects[name]
        mapping = SCOPE_COLUMNS.get(name)
        print(f"\n{schema_of[name]}.{name}")

        if name in UNSCOPED_OBJECTS:
            print(f"  {DIM}declared unscoped -- readable in full by every user{RESET}")
            continue

        if mapping is None and name in INTENTIONALLY_UNMAPPED:
            print(f"  {DIM}refused for scoped users by design "
                  f"(INTENTIONALLY_UNMAPPED){RESET}")
            continue

        if mapping is None:
            print(f"  {RED}NOT MAPPED{RESET} -- scoped users will get an RLSError here.")
            candidates = [c for c in ("region", "subregion", "salesperson_id",
                                      "supervisor_name", "sales_channel", "clientsd_id")
                          if c in cols]
            if candidates:
                print(f"  {DIM}candidate columns: {', '.join(candidates)}{RESET}")
            else:
                print(f"  {DIM}no obvious scope column -- add to UNSCOPED_OBJECTS if harmless{RESET}")
            problems += 1
            continue

        for dim in WANTED:
            target = mapping.get(dim)
            if target is None:
                print(f"  {YELLOW}·{RESET} {dim:<14} not enforceable")
            elif isinstance(target, Via):
                ok = target.local_column in cols
                mark = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
                print(f"  {mark} {dim:<14} via {target.local_column} -> "
                      f"{target.source}.{target.source_column}")
                if not ok:
                    print(f"      {RED}column '{target.local_column}' does not exist{RESET}")
                    problems += 1
            else:
                ok = str(target).lower() in cols
                mark = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
                print(f"  {mark} {dim:<14} {target}")
                if not ok:
                    print(f"      {RED}column '{target}' does not exist{RESET}")
                    problems += 1

    stale = sorted(set(SCOPE_COLUMNS) - set(objects))
    if stale:
        print(f"\n{YELLOW}Mapped but absent from the DB:{RESET} {', '.join(stale)}")

    print(f"\n{'-' * 60}")
    if problems:
        print(f"{RED}{problems} problem(s) found.{RESET} Fix rls.py, or add the "
              f"missing columns to the model under sqlmesh/models/bi/ and "
              f"re-run `sqlmesh plan`.")
        return 1
    print(f"{GREEN}Scope map is consistent with the lake.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())