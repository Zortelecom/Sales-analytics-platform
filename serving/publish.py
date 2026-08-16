"""
Publish the bi.* semantic layer to files, for consumers that cannot attach the lake.

WHAT THIS REPLACES
──────────────────
ServingLayerSync copied every mart table out of DuckLake into a standalone
serving.db, then applied bi_views.sql on top. That existed because a DuckDB
file catalog admits one writer, so BI tools could not read the lake while the
pipeline wrote to it.

With the views defined as SQLMesh models inside the lake, the copy is only
needed for tools that cannot speak DuckDB at all:

    Streamlit, Superset, Metabase, DBeaver  ->  ATTACH the lake directly
    Power BI                                ->  Parquet published here
    Excel                                   ->  CSV published here

WHAT GETS PUBLISHED
───────────────────
BOTH schemas, into one directory:

    marts.*  the star schema -- fact_sales, dim_products, dim_date, ...
             Power BI's semantic model is built on these and does its own
             modelling in DAX. It does NOT read the bi.* views.
    bi.*     the flat semantic views. Excel and any ad-hoc consumer that
             wants a denormalised table rather than a star schema.

They share a directory because the names cannot collide (fact_*/dim_* vs v_*)
and because Power BI's existing file paths then do not change.

So this module publishes FILES, not a database. No temp file, no atomic
rename, no Quack, no post-sync view script.

WHY NOT KEEP serving.db
───────────────────────
Every copy is a chance for the copy to disagree with the source, and the
serving DB had no lineage: nothing connected a stale number in Power BI back
to the model that produced it. bi_views.sql proved the point -- it selected
fact_sales.unit_price_actual months after that column was renamed, and applied
cleanly every night because it was raw SQL outside SQLMesh's knowledge.

PREREQUISITE FOR CONCURRENT READS
─────────────────────────────────
Attaching the lake read-only while SQLMesh writes requires the PostgreSQL
catalog (docs/LAYER3_SERVING.md, phase B). Until then, run the publish after
the pipeline, and have interactive tools read the published Parquet.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import duckdb

from shared.env import load_env, resolve_path
from shared.paths import DATA_DIR, WAREHOUSE_DIR

load_env()
logger = logging.getLogger(__name__)

CATALOG_ALIAS = "sales_lakehouse"


@dataclass
class PublishConfig:
    environment: str = "dev"
    catalog_path: Path = None
    data_path: Path = None
    export_root: Path = None
    formats: List[str] = None
    schemas: List[str] = None     # logical names: "marts", "bi"
    compression: str = "zstd"
    csv_delimiter: str = ";"      # French Excel: semicolon, not comma
    include: Optional[List[str]] = None

    def __post_init__(self):
        self.catalog_path = self.catalog_path or resolve_path(
            os.getenv("DUCKLAKE_CATALOG_PATH"), WAREHOUSE_DIR / "catalog.ducklake")
        self.data_path = self.data_path or resolve_path(
            os.getenv("PARQUET_PATH"), WAREHOUSE_DIR / "parquet")
        self.export_root = self.export_root or DATA_DIR / "exports"
        self.formats = self.formats or ["parquet"]
        self.schemas = self.schemas or ["marts", "bi"]

    def physical_schema(self, logical: str) -> str:
        """
        SQLMesh namespaces non-prod environments: marts__dev, bi__dev.
        prod keeps the bare name.
        """
        return logical if self.environment == "prod" else f"{logical}__{self.environment}"


def connect(config: PublishConfig, read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """
    Attach the lake. READ_ONLY by default -- a publish must never be able to
    modify the warehouse it is reading.

    DATA_PATH is not passed: it is stored in the catalog at creation, and
    passing a different value is how ingestion and SQLMesh once ended up
    pointed at two different directories.
    """
    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    # READ_ONLY is an ATTACH OPTION and goes in parentheses. A comma is a
    # parser error, not a silently-ignored flag.
    options = " (READ_ONLY)" if read_only else ""
    con.execute(
        f"ATTACH 'ducklake:{config.catalog_path.as_posix()}' AS {CATALOG_ALIAS}{options}"
    )
    con.execute(f"USE {CATALOG_ALIAS}")
    return con


def list_objects(con, config: PublishConfig) -> List[tuple]:
    """
    Every publishable object as (physical_schema, name).

    marts.* are tables, bi.* are views, so both catalogs are consulted.
    Internal SQLMesh objects (leading underscore) are excluded.
    """
    found: List[tuple] = []
    for logical in config.schemas:
        schema = config.physical_schema(logical)
        rows = con.execute(
            "SELECT table_name FROM duckdb_tables() WHERE schema_name = ? "
            "UNION ALL "
            "SELECT view_name FROM duckdb_views() WHERE schema_name = ? "
            "ORDER BY 1",
            [schema, schema],
        ).fetchall()
        names = [r[0] for r in rows if not r[0].startswith("_")]
        if config.include:
            names = [n for n in names if n in config.include]
        if not names:
            logger.warning("Nothing to publish in %s", schema)
        found.extend((schema, n) for n in names)
    return found


def publish(config: PublishConfig) -> Dict[str, Dict[str, int]]:
    """Write every bi.* view to the requested formats. Returns rows per view."""
    started = datetime.now(timezone.utc)
    results: Dict[str, Dict[str, int]] = {}

    with connect(config) as con:
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
                source = f'"{schema}"."{name}"'
                if fmt == "parquet":
                    con.execute(
                        f"COPY {source} TO '{target.as_posix()}' "
                        f"(FORMAT PARQUET, COMPRESSION '{config.compression}')"
                    )
                elif fmt == "csv":
                    con.execute(
                        f"COPY {source} TO '{target.as_posix()}' "
                        f"(FORMAT CSV, HEADER TRUE, DELIMITER '{config.csv_delimiter}')"
                    )
                else:
                    raise ValueError(f"Unknown format: {fmt}")

                rows = con.execute(f"SELECT COUNT(*) FROM {source}").fetchone()[0]
                results.setdefault(name, {})[fmt] = rows
                logger.info("  %-26s %-8s %8d rows  %7.1f KB",
                            name, fmt, rows, target.stat().st_size / 1024)

        # A manifest, so a consumer can tell how old its data is without
        # guessing from file mtimes.
        manifest = config.export_root / f"_manifest_{config.environment}.txt"
        manifest.write_text(
            f"published_at: {started.isoformat()}\n"
            f"environment:  {config.environment}\n"
            f"catalog:      {config.catalog_path}\n"
            f"schemas:      {', '.join(config.physical_schema(s) for s in config.schemas)}\n"
            f"formats:      {', '.join(config.formats)}\n\n"
            + "\n".join(f"{v:<28} {r}" for v, r in sorted(results.items())),
            encoding="utf-8",
        )
        logger.info("Manifest: %s", manifest)

    return results


def verify(config: PublishConfig) -> bool:
    """Report what is publishable right now. Reads only."""
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
    parser.add_argument("--schemas", nargs="*", default=["marts", "bi"],
                        choices=["marts", "bi"],
                        help="Which schemas to publish. Default: both. "
                             "Power BI reads marts; Excel usually reads bi.")
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