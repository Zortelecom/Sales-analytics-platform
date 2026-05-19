"""
Export event definitions.

A SyncCompletedEvent is emitted by ServingLayerSync at the end of every
successful (or partial) sync.  Exporters subscribe to it via ExportEventBus
and each runs independently and asynchronously.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List


@dataclass(frozen=True)
class SyncCompletedEvent:
    """
    Immutable snapshot of what was just synced.

    Attributes:
        environment:  SQLMesh environment (dev, prod, …).
        serving_path: Absolute path to the serving DuckDB file that now
                      holds the freshly synced tables.
        bi_schema:    DuckDB schema inside serving_path (default: "bi").
        tables:       Names of the tables that were successfully synced.
                      Excludes tables that failed during the sync.
        synced_at:    UTC-ish timestamp when the sync completed.
        mode:         Sync strategy used ("quack" or "file-swap").
        row_counts:   Optional per-table row counts (table_name → count).
    """

    environment: str
    serving_path: str
    bi_schema: str
    tables: List[str]
    synced_at: datetime = field(default_factory=datetime.now)
    mode: str = "file-swap"
    row_counts: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return (
            f"SyncCompletedEvent("
            f"env={self.environment}, "
            f"mode={self.mode}, "
            f"tables={len(self.tables)}, "
            f"at={self.synced_at:%Y-%m-%d %H:%M:%S}"
            f")"
        )
