"""
Publish the serving layer to files, for consumers that cannot attach the lake.


WHAT GETS PUBLISHED
───────────────────
BOTH schemas, into one directory:

    marts.*   the star schema -- fact_sales, dim_products, dim_date, ...
              Power BI's semantic model is built on these and does its own
              modelling in DAX. It does NOT read the bi.* views.
    bi.*      the flat semantic views. Excel and any ad-hoc consumer that
              wants a denormalised table rather than a star schema.
    reports.* the pre-shaped rep_* views -- target attainment, the weekly
              meeting pack, top products. Added 2026-08: SQLMesh had been
              building them on every plan while nothing validated or exported
              them, which is strictly worse than not building them at all.

They share a directory because the names cannot collide -- marts is
fact_*/dim_*, bi is v_*, reports is rep_* -- and because Power BI's existing
file paths then do not change.

So this module publishes FILES, not a database. No temp file, no atomic
rename, no Quack, no post-sync view script.

"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import duckdb

from shared import lake
from shared.env import load_env
from shared.paths import DATA_DIR

load_env()
logger = logging.getLogger(__name__)

CATALOG_ALIAS = lake.CATALOG_ALIAS


@dataclass
class PublishConfig:
    environment: str = "dev"
    export_root: Path = None
    formats: List[str] = None
    schemas: List[str] = None     # logical names: "marts", "bi", "reports"
    compression: str = "zstd"
    csv_delimiter: str = ";"      # French Excel: semicolon, not comma
    include: Optional[List[str]] = None

    # NO catalog_path / data_path HERE, DELIBERATELY.
    #
    # They used to be resolved from DUCKLAKE_CATALOG_PATH / PARQUET_PATH and
    # then never used to connect -- connect() goes through shared/lake.py,
    # which follows PG_CATALOG_HOST. The only consumer was the manifest, which
    # therefore stamped every Power BI export with the path of the DuckDB file
    # catalog even when the rows had come out of PostgreSQL. A provenance field
    # that is wrong is worse than no provenance field, and this one is read by
    # people trying to explain a number.
    #
    # lake.describe(role) is the single source of truth for "where did this
    # come from", and it renders both backends without a password.

    def __post_init__(self):
        self.export_root = self.export_root or DATA_DIR / "exports"
        self.formats = self.formats or ["parquet"]
        self.schemas = self.schemas or ["marts", "bi", "reports"]

    def physical_schema(self, logical: str) -> str:
        """
        SQLMesh namespaces non-prod environments: marts__dev, bi__dev.
        prod keeps the bare name.
        """
        return logical if self.environment == "prod" else f"{logical}__{self.environment}"


def connect(config: PublishConfig, read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """
    Attach the lake through shared/lake.py, read-only.

    The ATTACH used to be built here. It is now in one place because the
    PostgreSQL form carries a credential, and four copies of a connection
    string is four places to leak one -- and four places for the DATA_PATH
    rule, which differs between the DuckDB and PostgreSQL backends, to be
    wrong.

    role="publisher": with per-role credentials configured, the Power BI
    export path cannot write to the warehouse and can be rotated without
    stopping ingestion. Falls back to the default credential when unset.
    """
    return lake.connect(read_only=read_only, role="publisher", alias=CATALOG_ALIAS)



def list_objects(con, config: PublishConfig) -> List[tuple]:
    """
    Every publishable object as (physical_schema, name).

    marts.* are tables, bi.* are views, so both catalogs are consulted.
    Internal SQLMesh objects (leading underscore) are excluded.

    FILTERED BY DATABASE, NOT ONLY BY SCHEMA. duckdb_tables() and
    duckdb_views() span every attached database. With the PostgreSQL catalog
    DuckLake attaches a second one for its own metadata
    (__ducklake_metadata_sales_lakehouse), so the result set is no longer just
    the lake. Nothing collides today only because that metadata sits in
    `public` while we ask for marts__dev / bi__dev -- that is the schema naming
    protecting us, not the query. Pinning the database makes the answer
    identical on both backends, which is the property this module needs.
    """
    found: List[tuple] = []
    for logical in config.schemas:
        schema = config.physical_schema(logical)
        rows = con.execute(
            "SELECT table_name FROM duckdb_tables() "
            "WHERE database_name = ? AND schema_name = ? "
            "UNION ALL "
            "SELECT view_name FROM duckdb_views() "
            "WHERE database_name = ? AND schema_name = ? "
            "ORDER BY 1",
            [CATALOG_ALIAS, schema, CATALOG_ALIAS, schema],
        ).fetchall()
        names = [r[0] for r in rows if not r[0].startswith("_")]
        if config.include:
            names = [n for n in names if n in config.include]
        if not names:
            logger.warning("Nothing to publish in %s", schema)
        found.extend((schema, n) for n in names)
    return found


def publish(config: PublishConfig) -> Dict[str, Dict[str, int]]:
    """Write every published object to the requested formats. Rows per object."""
    started = datetime.now(timezone.utc)
    results: Dict[str, Dict[str, int]] = {}

    source = lake.describe("publisher")

    with connect(config) as con:
        # Stated once, up front, on every run: which catalog and which role.
        # The whole point of the migration is that this can change without any
        # code changing, so the log has to say which way it went.
        logger.info("Source: %s", source)

        objects = list_objects(con, config)
        if not objects:
            raise RuntimeError(
                f"Nothing found in {config.schemas} for env {config.environment}. "
                f"Has `sqlmesh plan {config.environment}` run?"
            )
        logger.info("Publishing %d object(s) from %s",
                    len(objects), ", ".join(config.schemas))

        for fmt in config.formats:
            out_dir = config.export_root / fmt / config.environment
            # Wipe first: a view deleted upstream must not leave a stale file
            # that Power BI keeps refreshing from.
            if out_dir.exists():
                shutil.rmtree(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)

            for schema, name in objects:
                target = out_dir / f"{name}.{fmt}"
                # `qualified`, NOT `source`. This was `source`, which is the
                # variable holding lake.describe("publisher") -- so by the time
                # the manifest below was written it had been clobbered with the
                # LAST object published, and every export was stamped
                #   catalog:      "bi__dev"."v_ytd_kpi"
                # instead of the catalog it came from. A provenance field that
                # is wrong is worse than no provenance field, and this one is
                # read by people trying to explain a number -- the exact
                # failure PublishConfig dropped catalog_path to avoid.
                qualified = f'"{schema}"."{name}"'
                if fmt == "parquet":
                    con.execute(
                        f"COPY {qualified} TO '{target.as_posix()}' "
                        f"(FORMAT PARQUET, COMPRESSION '{config.compression}')"
                    )
                elif fmt == "csv":
                    con.execute(
                        f"COPY {qualified} TO '{target.as_posix()}' "
                        f"(FORMAT CSV, HEADER TRUE, DELIMITER '{config.csv_delimiter}')"
                    )
                else:
                    raise ValueError(f"Unknown format: {fmt}")

                rows = con.execute(f"SELECT COUNT(*) FROM {qualified}").fetchone()[0]
                results.setdefault(name, {})[fmt] = rows
                logger.info("  %-26s %-8s %8d rows  %7.1f KB",
                            name, fmt, rows, target.stat().st_size / 1024)

        # A manifest, so a consumer can tell how old its data is without
        # guessing from file mtimes.
        manifest = config.export_root / f"_manifest_{config.environment}.txt"
        manifest.write_text(
            f"published_at: {started.isoformat()}\n"
            f"environment:  {config.environment}\n"
            f"catalog:      {source}\n"
            f"schemas:      {', '.join(config.physical_schema(s) for s in config.schemas)}\n"
            f"formats:      {', '.join(config.formats)}\n\n"
            + "\n".join(f"{v:<28} {r}" for v, r in sorted(results.items())),
            encoding="utf-8",
        )
        logger.info("Manifest: %s", manifest)

    return results


def verify(config: PublishConfig) -> bool:
    """Report what is publishable right now. Reads only."""
    print(f"Source: {lake.describe('publisher')}")
    with connect(config) as con:
        objects = list_objects(con, config)
        if not objects:
            logger.error("Nothing publishable for env %s", config.environment)
            return False
        empty = []
        current = None
        for schema, name in objects:
            if schema != current:
                print(f"\n{schema}")
                current = schema
            rows = con.execute(f'SELECT COUNT(*) FROM "{schema}"."{name}"').fetchone()[0]
            print(f"  {name:<28} {rows:>10,} rows")
            if rows == 0:
                empty.append(f"{schema}.{name}")
    if empty:
        logger.warning("Empty views: %s", empty)
    return not empty


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="dev")
    parser.add_argument("--parquet", action="store_true", help="Publish Parquet (Power BI)")
    parser.add_argument("--csv", action="store_true", help="Publish CSV (Excel)")
    parser.add_argument("--compression", default="zstd",
                        choices=["snappy", "zstd", "gzip", "brotli", "lz4", "uncompressed"])
    parser.add_argument("--csv-delimiter", default=";",
                        help="Default ';' for French-locale Excel")
    parser.add_argument("--schemas", nargs="*",
                        default=["marts", "bi", "reports"],
                        choices=["marts", "bi", "reports"],
                        help="Which schemas to publish. Default: all three. "
                             "Power BI reads marts; Excel usually reads bi; "
                             "reports holds the pre-shaped rep_* views.")
    parser.add_argument("--only", nargs="*", metavar="NAME",
                        help="Publish only these tables/views")
    parser.add_argument("--verify", action="store_true",
                        help="Report the semantic layer and exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    formats = [f for f, on in (("parquet", args.parquet), ("csv", args.csv)) if on]
    config = PublishConfig(
        environment=args.env,
        formats=formats or ["parquet"],
        schemas=args.schemas,
        compression=args.compression,
        csv_delimiter=args.csv_delimiter,
        include=args.only,
    )

    if args.verify:
        return 0 if verify(config) else 1

    try:
        publish(config)
    except Exception as exc:  # noqa: BLE001
        logger.error("Publish failed: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())