"""
Parquet Export Service.

Listens for SyncCompletedEvent and writes each mart table to a .parquet file.
Intended for data scientists, downstream pipelines, or any tool that can read
Parquet natively (pandas, Polars, Spark, DuckDB, Arrow, …).

Usage — register with the event bus::

    from serving.export.parquet_exporter import ParquetExporter
    from serving.export.event_bus import ExportEventBus

    bus = ExportEventBus()
    bus.subscribe(ParquetExporter(
        output_path="data/exports/parquet/dev",
        compression="snappy",
    ))
    bus.publish(event)

Standalone (one-off, outside the pipeline)::

    import asyncio
    from serving.export.parquet_exporter import ParquetExporter
    from serving.export.events import SyncCompletedEvent

    exporter = ParquetExporter(
        output_path="data/exports/parquet/dev",
        compression="zstd",
        row_group_size=200_000,
    )
    event = SyncCompletedEvent(
        environment="dev",
        serving_path="data/warehouse/serving_dev.db",
        bi_schema="bi",
        tables=["fact_sales", "dim_products"],
    )
    result = asyncio.run(exporter.export(event))
    print(result)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Literal, Optional

from .base_exporter import BaseExporter

logger = logging.getLogger(__name__)

# Valid compression codecs supported by DuckDB's Parquet writer.
VALID_COMPRESSIONS = {"snappy", "gzip", "brotli", "zstd", "lz4", "none", "uncompressed"}

CompressionCodec = Literal["snappy", "gzip", "brotli", "zstd", "lz4", "none", "uncompressed"]


class ParquetExporter(BaseExporter):
    """
    Exports mart tables to Parquet files.

    Each table is written as `<output_path>/<table_name>.parquet`.
    DuckDB's native COPY … TO (FORMAT PARQUET) is used — no Pandas, no Arrow
    Python objects; data is serialised directly from DuckDB's columnar buffers.

    Args:
        output_path:    Directory where .parquet files are written.
        compression:    Compression codec.  "snappy" is fast with decent ratio;
                        "zstd" gives better compression at slightly higher CPU;
                        "uncompressed" maximises read speed at the cost of size.
                        Default: "snappy".
        row_group_size: Number of rows per Parquet row-group.  Larger values
                        improve compression and sequential reads; smaller values
                        improve random / predicate-pushdown reads.  Default: 122_880
                        (DuckDB's own default, ~128 MB per group for wide tables).
        field_ids:      When True, embed Parquet field IDs so readers like Iceberg
                        or AWS Glue can track column renames.  Default: False.
        include_tables: If set, only these tables are exported.
        exclude_tables: Tables to skip (ignored if include_tables is set).
        clean_on_run:   Wipe output_path before each run.  Default: True.

    Output layout::

        data/exports/parquet/dev/
        ├── fact_sales.parquet
        ├── dim_products.parquet
        ├── dim_clientsd.parquet
        └── …

    Compression trade-offs::

        snappy      — fast compress/decompress, ~2× smaller than raw.  Best default.
        zstd        — ~3-4× smaller than raw, moderate CPU.  Good for archival.
        gzip        — widely supported, slower than zstd.  Use for legacy readers.
        uncompressed— fastest reads, largest files.  Only for hot/local analytics.
    """

    format = "parquet"

    def __init__(
        self,
        output_path: str,
        compression: CompressionCodec = "snappy",
        row_group_size: Optional[int] = None,
        field_ids: bool = False,
        include_tables: Optional[List[str]] = None,
        exclude_tables: Optional[List[str]] = None,
        clean_on_run: bool = True,
    ) -> None:
        if compression not in VALID_COMPRESSIONS:
            raise ValueError(
                f"Invalid compression '{compression}'. "
                f"Must be one of: {sorted(VALID_COMPRESSIONS)}"
            )

        super().__init__(
            output_path=output_path,
            include_tables=include_tables,
            exclude_tables=exclude_tables,
            clean_on_run=clean_on_run,
        )
        self.compression = compression
        self.row_group_size = row_group_size
        self.field_ids = field_ids

    # ------------------------------------------------------------------

    def export_table(self, conn, table_name: str, bi_schema: str, output_dir: Path) -> None:
        """
        Write `bi_schema.table_name` to `output_dir/table_name.parquet`.

        Uses DuckDB's native COPY … TO (FORMAT PARQUET) which serialises data
        directly from DuckDB's internal columnar buffers to disk — zero Python
        object allocation for the table data itself.
        """
        out_file = output_dir / f"{table_name}.parquet"
        full_table = f"{bi_schema}.{table_name}"

        # Build COPY options dynamically so we only include the options we set.
        options = [
            f"FORMAT PARQUET",
            f"COMPRESSION '{self.compression}'",
        ]
        if self.row_group_size is not None:
            options.append(f"ROW_GROUP_SIZE {self.row_group_size}")
        if self.field_ids:
            options.append("FIELD_IDS auto")

        options_sql = ", ".join(options)

        conn.execute(f"""
            COPY {full_table}
            TO '{out_file}'
            ({options_sql})
        """)

        size_mb = out_file.stat().st_size / (1_024 ** 2)
        logger.info(
            "    🦆 %s → %s (%.2f MB, %s)",
            full_table, out_file.name, size_mb, self.compression,
        )
