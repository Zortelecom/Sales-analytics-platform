"""
CSV Export Service.

Listens for SyncCompletedEvent and writes each mart table to a .csv file.
Intended for users who load data into Excel, Power Query, or any tool that
doesn't speak DuckDB natively.

Usage — register with the event bus::

    from serving.export.csv_exporter import CsvExporter
    from serving.export.event_bus import ExportEventBus

    bus = ExportEventBus()
    bus.subscribe(CsvExporter(output_path="data/exports/csv/dev"))
    bus.publish(event)          # called automatically by ServingLayerSync

Standalone (one-off, outside the pipeline)::

    import asyncio, duckdb
    from serving.export.csv_exporter import CsvExporter
    from serving.export.events import SyncCompletedEvent

    exporter = CsvExporter(output_path="data/exports/csv/dev")
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
from typing import List, Optional

from .base_exporter import BaseExporter

logger = logging.getLogger(__name__)


class CsvExporter(BaseExporter):
    """
    Exports mart tables to CSV files.

    Each table is written as `<output_path>/<table_name>.csv` with a header
    row, comma delimiter, and double-quote quoting — ready for Excel, Power
    Query, Pandas, or any standard CSV reader.

    Args:
        output_path:    Directory where .csv files are written.
        delimiter:      Column separator.  Default: comma.
        include_header: Whether to include the column-name row.  Default: True.
        quoting:        DuckDB QUOTE character.  Default: double-quote.
        null_value:     String written for NULL cells.  Default: empty string.
        encoding:       File encoding passed to DuckDB COPY.  Default: UTF-8.
        include_tables: If set, only these tables are exported.
        exclude_tables: Tables to skip (ignored if include_tables is set).
        clean_on_run:   Wipe output_path before each run.  Default: True.

    Output layout::

        data/exports/csv/dev/
        ├── fact_sales.csv
        ├── dim_products.csv
        ├── dim_clientsd.csv
        └── …
    """

    format = "csv"

    def __init__(
        self,
        output_path: str,
        delimiter: str = ",",
        include_header: bool = True,
        quoting: str = '"',
        null_value: str = "",
        encoding: str = "UTF-8",
        include_tables: Optional[List[str]] = None,
        exclude_tables: Optional[List[str]] = None,
        clean_on_run: bool = True,
    ) -> None:
        super().__init__(
            output_path=output_path,
            include_tables=include_tables,
            exclude_tables=exclude_tables,
            clean_on_run=clean_on_run,
        )
        self.delimiter = delimiter
        self.include_header = include_header
        self.quoting = quoting
        self.null_value = null_value
        self.encoding = encoding

    # ------------------------------------------------------------------

    def export_table(self, conn, table_name: str, bi_schema: str, output_dir: Path) -> None:
        """
        Write `bi_schema.table_name` to `output_dir/table_name.csv`.

        Uses DuckDB's native COPY … TO, which streams data directly from the
        in-process connection to disk without materialising a Python object.
        """
        out_file = output_dir / f"{table_name}.csv"
        full_table = f"{bi_schema}.{table_name}"

        header_opt = "TRUE" if self.include_header else "FALSE"

        conn.execute(f"""
            COPY {full_table}
            TO '{out_file}'
            (
                FORMAT      CSV,
                HEADER      {header_opt},
                DELIMITER   '{self.delimiter}',
                QUOTE       '{self.quoting}',
                NULLSTR     '{self.null_value}'
            )
        """)

        size_kb = out_file.stat().st_size / 1_024
        logger.info(
            "    📄 %s → %s (%.1f KB)",
            full_table, out_file.name, size_kb,
        )
