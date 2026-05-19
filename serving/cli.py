#!/usr/bin/env python3
"""
CLI for serving layer operations.

Subcommands
-----------
  sync      Sync DuckLake marts to serving.db (file-swap or Quack mode).
            Optionally triggers CSV/Parquet exports via the export bus.
  serve     Start a persistent Quack server backed by serving.db.
  validate  Check that serving.db is queryable and recent.
  stats     Print aggregate statistics from the last sync.
  export    One-off export from an already-synced serving.db
            (no re-sync required).

Examples
--------
  # Default: file-swap sync, no exports
  python -m serving.cli sync --env dev

  # Sync + both exports
  python -m serving.cli sync --env dev --csv --parquet

  # Sync + only CSV (for Excel users), Parquet not needed
  python -m serving.cli sync --env dev --csv

  # Quack mode + both exports, background (don't wait for exports)
  python -m serving.cli sync --env prod --quack --quack-token s3cr3t \\
      --csv --parquet --export-background

  # Start a persistent Quack server (before Streamlit / Power BI)
  python -m serving.cli serve --env dev --quack-token s3cr3t

  # One-off export from existing serving.db (no re-sync)
  python -m serving.cli export --env dev --csv --parquet

  # Validate freshness
  python -m serving.cli validate --env dev
"""

import argparse
import signal
import sys
import logging
from pathlib import Path

from .sync import ServingLayerSync, QuackServer
from .config import ServingConfig, QuackConfig
from .export.event_bus import ExportEventBus
from .export.events import SyncCompletedEvent
from .export.csv_exporter import CsvExporter
from .export.parquet_exporter import ParquetExporter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared argument helpers
# ---------------------------------------------------------------------------

def _add_env_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--env", default="dev",
        help="SQLMesh environment (dev, prod, staging, ...). Default: dev."
    )


def _add_quack_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("Quack options (DuckDB >= v1.5.2 beta)")
    g.add_argument(
        "--quack", action="store_true",
        help="Enable Quack client-server mode. Requires a running Quack server."
    )
    g.add_argument("--quack-host", default="localhost", metavar="HOST")
    g.add_argument("--quack-port", type=int, default=9494, metavar="PORT")
    g.add_argument(
        "--quack-token", default="change_me_in_production", metavar="TOKEN",
        help="Shared auth token (min 4 chars)."
    )


def _add_export_args(p: argparse.ArgumentParser) -> None:
    """
    Export flags are additive and independent of each other.
    Not passing either flag disables all exports (no CSV, no Parquet).
    """
    g = p.add_argument_group("Export options (optional, async, event-triggered)")
    g.add_argument(
        "--csv", action="store_true",
        help="Export mart tables to CSV files (for Excel, Power Query, ...)."
    )
    g.add_argument(
        "--csv-path", default=None, metavar="DIR",
        help="Override CSV output directory. Default: data/exports/csv/<env>/"
    )
    g.add_argument(
        "--csv-delimiter", default=",", metavar="CHAR",
        help="CSV column separator. Default: comma."
    )
    g.add_argument(
        "--parquet", action="store_true",
        help="Export mart tables to Parquet files (for analytics, data science, ...)."
    )
    g.add_argument(
        "--parquet-path", default=None, metavar="DIR",
        help="Override Parquet output directory. Default: data/exports/parquet/<env>/"
    )
    g.add_argument(
        "--parquet-compression",
        default="snappy",
        choices=["snappy", "gzip", "brotli", "zstd", "lz4", "uncompressed"],
        metavar="CODEC",
        help="Parquet compression codec. Default: snappy."
    )
    g.add_argument(
        "--export-background", action="store_true",
        help=(
            "Fire exports in a background thread and return immediately. "
            "Default: wait for exports before exiting (publish_and_wait)."
        )
    )
    g.add_argument(
        "--export-timeout", type=int, default=300, metavar="SECONDS",
        help="Timeout in seconds for export completion (default: 300)."
    )


def _build_config(args: argparse.Namespace) -> ServingConfig:
    quack_cfg = None
    if getattr(args, "quack", False):
        quack_cfg = QuackConfig(
            host=args.quack_host,
            port=args.quack_port,
            token=args.quack_token,
        )
    config = ServingConfig(environment=args.env, quack=quack_cfg)
    config.normalize()
    return config


def _build_export_bus(args: argparse.Namespace, config: ServingConfig) -> ExportEventBus:
    """
    Construct an ExportEventBus from CLI flags.
    Returns an empty bus (no exporters) if neither --csv nor --parquet is set.
    This makes exports completely optional: the pipeline works identically
    without them.
    """
    timeout = getattr(args, "export_timeout", 300)
    bus = ExportEventBus(timeout_seconds=timeout)

    if getattr(args, "csv", False):
        csv_path = getattr(args, "csv_path", None) or config.csv_export.path
        bus.subscribe(CsvExporter(
            output_path=csv_path,
            delimiter=getattr(args, "csv_delimiter", ","),
        ))
        logger.info("CSV exporter registered -> %s", csv_path)

    if getattr(args, "parquet", False):
        parquet_path = getattr(args, "parquet_path", None) or config.parquet_export.path
        bus.subscribe(ParquetExporter(
            output_path=parquet_path,
            compression=getattr(args, "parquet_compression", "snappy"),
        ))
        logger.info("Parquet exporter registered -> %s", parquet_path)

    if bus.subscriber_count == 0:
        logger.info("No export flags set -- exports disabled.")

    return bus


# ---------------------------------------------------------------------------
# Subcommand: sync
# ---------------------------------------------------------------------------

def cmd_sync(args: argparse.Namespace) -> int:
    config = _build_config(args)
    export_bus = _build_export_bus(args, config)

    # Patch: use background dispatch if requested
    background = getattr(args, "export_background", False)
    if background and export_bus.subscriber_count > 0:
        logger.info("Exports will run in background (non-blocking).")

    sync = ServingLayerSync(config, export_bus=export_bus if not background else None)
    result = sync.sync(dry_run=args.dry_run)

    # If background mode: fire exports manually after sync
    if background and not args.dry_run and export_bus.subscriber_count > 0:
        if result.get("status") in ("success", "partial_success"):
            _fire_background_exports(export_bus, sync, result)

    status = result.get("status", "error")
    logger.info("Sync result: %s", result)
    return 0 if status in ("success", "partial_success", "dry_run") else 1


def _fire_background_exports(bus: ExportEventBus, sync: ServingLayerSync, summary: dict) -> None:
    """Build a SyncCompletedEvent and publish it in a background thread."""
    succeeded = [m["table"] for m in sync.sync_metadata if m.get("status") == "success"]
    event = SyncCompletedEvent(
        environment=sync.config.environment,
        serving_path=sync.config.serving_path,
        bi_schema=sync.config.bi_schema,
        tables=succeeded,
        mode=summary.get("mode", "unknown"),
    )
    thread = bus.publish_background(event)
    logger.info("Background exports started (thread: %s). Pipeline continuing.", thread.name)


# ---------------------------------------------------------------------------
# Subcommand: export  (one-off, no re-sync)
# ---------------------------------------------------------------------------

def cmd_export(args: argparse.Namespace) -> int:
    """
    Export tables from an already-synced serving.db without re-running the sync.

    Useful for:
    - Regenerating CSVs after changing delimiter or encoding.
    - Producing Parquet exports from an existing serving.db on demand.
    - Ad-hoc "someone needs the Excel file NOW" requests.
    """
    config = _build_config(args)
    export_bus = _build_export_bus(args, config)

    if export_bus.subscriber_count == 0:
        logger.error("No export format specified. Use --csv and/or --parquet.")
        return 1

    serving_path = Path(config.serving_path)
    if not serving_path.exists():
        logger.error("Serving DB not found: %s. Run `sync` first.", serving_path)
        return 1

    # Discover which tables are in serving.db
    import duckdb
    conn = duckdb.connect(str(serving_path), read_only=True)
    tables_raw = conn.execute(f"""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = ? AND table_name NOT LIKE '\_%' ESCAPE '\\'
        ORDER BY table_name
    """, [config.bi_schema]).fetchall()
    conn.close()
    tables = [r[0] for r in tables_raw]

    if not tables:
        logger.warning("No tables found in '%s.%s'.", serving_path.name, config.bi_schema)
        return 1

    logger.info("Exporting %d table(s) from %s ...", len(tables), serving_path)

    event = SyncCompletedEvent(
        environment=config.environment,
        serving_path=str(serving_path),
        bi_schema=config.bi_schema,
        tables=tables,
        mode="standalone-export",
    )

    try:
        results = export_bus.publish_and_wait(event)
        all_ok = all(r.status in ("success", "partial") for r in results.values())
        return 0 if all_ok else 1
    except Exception as exc:
        logger.error("Export failed: %s", exc)
        return 1


# ---------------------------------------------------------------------------
# Subcommand: serve
# ---------------------------------------------------------------------------

def cmd_serve(args: argparse.Namespace) -> int:
    quack_cfg = QuackConfig(
        host=args.quack_host,
        port=args.quack_port,
        token=args.quack_token,
    )
    config = ServingConfig(environment=args.env, quack=quack_cfg)
    config.normalize()

    logger.info(
        "Starting Quack server | env=%s | uri=%s",
        config.environment, quack_cfg.uri,
    )
    logger.info(
        "Connect with:\n"
        "    INSTALL quack FROM core_nightly;\n"
        "    LOAD quack;\n"
        "    CREATE SECRET (TYPE quack, TOKEN '%s');\n"
        "    ATTACH '%s' AS serving;",
        quack_cfg.token, quack_cfg.uri,
    )

    server = QuackServer(serving_path=config.serving_path, quack=quack_cfg)
    server.start()

    stop_requested = []

    def _handle(signum, frame):
        logger.info("Signal %d -- stopping ...", signum)
        stop_requested.append(True)

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    logger.info("Quack server running on %s. Ctrl-C to stop.", quack_cfg.uri)
    import time
    while not stop_requested:
        time.sleep(1)

    server.stop()
    return 0


# ---------------------------------------------------------------------------
# Subcommand: validate
# ---------------------------------------------------------------------------

def cmd_validate(args: argparse.Namespace) -> int:
    config = _build_config(args)
    sync = ServingLayerSync(config)
    return 0 if sync.validate_serving_db() else 1


# ---------------------------------------------------------------------------
# Subcommand: stats
# ---------------------------------------------------------------------------

def cmd_stats(args: argparse.Namespace) -> int:
    config = _build_config(args)
    sync = ServingLayerSync(config)
    stats = sync.get_sync_stats()
    if not stats:
        logger.warning("No sync stats found. Has the pipeline run yet?")
        return 1
    print("\n Sync Statistics")
    print("-" * 40)
    for key, val in stats.items():
        print(f"  {key:<25} {val}")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="serving.cli",
        description="SQLMesh-aligned DuckLake Serving Layer CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # sync
    p_sync = sub.add_parser("sync", help="Sync DuckLake marts -> serving.db.")
    _add_env_arg(p_sync)
    _add_quack_args(p_sync)
    _add_export_args(p_sync)
    p_sync.add_argument("--dry-run", action="store_true")
    p_sync.set_defaults(func=cmd_sync)

    # export
    p_export = sub.add_parser(
        "export",
        help="One-off export from existing serving.db (no re-sync)."
    )
    _add_env_arg(p_export)
    _add_export_args(p_export)
    p_export.set_defaults(func=cmd_export)

    # serve
    p_serve = sub.add_parser("serve", help="Start a persistent Quack server.")
    _add_env_arg(p_serve)
    p_serve.add_argument("--quack-host", default="localhost")
    p_serve.add_argument("--quack-port", type=int, default=9494)
    p_serve.add_argument("--quack-token", default="change_me_in_production")
    p_serve.set_defaults(func=cmd_serve)

    # validate
    p_val = sub.add_parser("validate", help="Check serving.db is fresh.")
    _add_env_arg(p_val)
    p_val.set_defaults(func=cmd_validate)

    # stats
    p_stats = sub.add_parser("stats", help="Print last-sync statistics.")
    _add_env_arg(p_stats)
    p_stats.set_defaults(func=cmd_stats)

    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
