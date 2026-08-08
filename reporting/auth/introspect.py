"""
reporting/auth/introspect.py

Audit `reporting/auth/rls.py`'s SCOPE_COLUMNS map against the real serving DB.

    python -m reporting.auth.introspect
    python -m reporting.auth.introspect --db data/warehouse/serving_dev.db

For every view and table it prints which scope dimensions can be enforced,
which are declared but reference a column that does not exist (a latent
crash), and which objects are in neither SCOPE_COLUMNS nor UNSCOPED_OBJECTS
(a latent RLSError for scoped users).

Run this whenever you change bi_views.sql.
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
from reporting.config import DB_PATH  # noqa: E402

# Dimensions we would like every revenue-bearing object to support.
WANTED = ("regions", "subregions", "salespersons", "supervisors", "channels", "clients")

GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"
if not sys.stdout.isatty() or os.name == "nt":
    GREEN = YELLOW = RED = DIM = RESET = ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    if not Path(args.db).exists():
        print(f"{RED}Serving DB not found: {args.db}{RESET}")
        return 1

    con = duckdb.connect(args.db, read_only=True)
    rows = con.execute(
        """
        SELECT table_schema, table_name, column_name
        FROM information_schema.columns
        WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
        ORDER BY table_schema, table_name, ordinal_position
        """
    ).fetchall()

    objects: dict[str, set[str]] = {}
    schema_of: dict[str, str] = {}
    for schema, table, col in rows:
        objects.setdefault(table.lower(), set()).add(col.lower())
        schema_of[table.lower()] = schema

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
        print(f"{RED}{problems} problem(s) found.{RESET} Fix rls.py or add the "
              f"missing columns to serving/templates/bi_views.sql.")
        return 1
    print(f"{GREEN}Scope map is consistent with the serving DB.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())