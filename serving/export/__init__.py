"""
serving.export — optional, async, event-triggered export services.

Quick start::

    from serving.export import ExportEventBus, CsvExporter, ParquetExporter

    bus = (
        ExportEventBus()
        .subscribe(CsvExporter("data/exports/csv/dev"))
        .subscribe(ParquetExporter("data/exports/parquet/dev", compression="zstd"))
    )

    # Pass the bus into ServingLayerSync:
    sync = ServingLayerSync(config, export_bus=bus)
    sync.sync()
    # → exports run automatically after sync completes.
"""

from .event_bus import ExportEventBus
from .events import SyncCompletedEvent
from .csv_exporter import CsvExporter
from .parquet_exporter import ParquetExporter
from .base_exporter import BaseExporter, ExportResult

__all__ = [
    "ExportEventBus",
    "SyncCompletedEvent",
    "CsvExporter",
    "ParquetExporter",
    "BaseExporter",
    "ExportResult",
]
