"""
ExportEventBus — wires sync completion events to export services.

The bus decouples ServingLayerSync from any specific export format.
Sync fires a SyncCompletedEvent; each registered exporter handles it
independently and concurrently.

Concurrency model
─────────────────
Exporters are async coroutines.  The bus runs them with asyncio.gather,
so CSV and Parquet (and any future format) export in parallel, bounded
only by disk I/O.

Two dispatch modes are available:

  publish_and_wait(event)   — await all exporters; raises on total failure.
                              Use this in the Dagster pipeline so the asset
                              doesn't complete until exports are done.

  publish_background(event) — fire-and-forget in a daemon thread.
                              Use this when you don't want exports to delay
                              the pipeline handoff (e.g. large Parquet files).

Both modes are safe to call from synchronous code.

Example (typical Dagster asset)::

    from serving.export.event_bus import ExportEventBus
    from serving.export.csv_exporter import CsvExporter
    from serving.export.parquet_exporter import ParquetExporter
    from serving.export.events import SyncCompletedEvent

    bus = ExportEventBus(timeout_seconds=120)
    bus.subscribe(CsvExporter(output_path="data/exports/csv/dev"))
    bus.subscribe(ParquetExporter(output_path="data/exports/parquet/dev"))

    # After sync completes:
    event = SyncCompletedEvent(
        environment="dev",
        serving_path="data/warehouse/serving_dev.db",
        bi_schema="bi",
        tables=synced_table_names,
    )
    results = bus.publish_and_wait(event)      # blocks until done
    # or:
    bus.publish_background(event)              # returns immediately

Example (disabled — no exporters registered)::

    bus = ExportEventBus()
    # Don't subscribe anything → publish_and_wait returns {} instantly.
    results = bus.publish_and_wait(event)

Example (only CSV, no Parquet)::

    bus = ExportEventBus()
    bus.subscribe(CsvExporter(output_path="data/exports/csv/dev"))
    # Parquet simply isn't registered.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Dict, List, Optional

from .base_exporter import BaseExporter, ExportResult
from .events import SyncCompletedEvent

logger = logging.getLogger(__name__)


class ExportEventBus:
    """
    Lightweight publish-subscribe bus for export services.

    Attributes:
        timeout_seconds: Maximum wall-clock seconds to wait for all exporters
                         in `publish_and_wait`.  None means no timeout.
    """

    def __init__(self, timeout_seconds: Optional[float] = 300) -> None:
        self._subscribers: List[BaseExporter] = []
        self.timeout_seconds = timeout_seconds

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    def subscribe(self, exporter: BaseExporter) -> "ExportEventBus":
        """
        Register an exporter.  Returns self for chaining.

        Example::

            bus = (
                ExportEventBus()
                .subscribe(CsvExporter("data/exports/csv/dev"))
                .subscribe(ParquetExporter("data/exports/parquet/dev"))
            )
        """
        self._subscribers.append(exporter)
        logger.debug(
            "Subscribed %s exporter (total: %d).",
            exporter.format.upper(), len(self._subscribers),
        )
        return self

    def unsubscribe(self, exporter: BaseExporter) -> None:
        """Remove a previously registered exporter."""
        self._subscribers.remove(exporter)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # ------------------------------------------------------------------
    # Dispatch — blocking
    # ------------------------------------------------------------------

    def publish_and_wait(self, event: SyncCompletedEvent) -> Dict[str, ExportResult]:
        """
        Dispatch event to all subscribers and wait for completion.

        Runs all exporter coroutines concurrently via asyncio.gather.
        Safe to call from synchronous code (creates a new event loop in
        a worker thread to avoid conflicts with existing loops).

        Args:
            event: The SyncCompletedEvent emitted by ServingLayerSync.

        Returns:
            Dict mapping exporter format → ExportResult.
            Empty dict if no subscribers are registered.

        Raises:
            TimeoutError: if exports exceed `self.timeout_seconds`.
        """
        if not self._subscribers:
            logger.debug("No export subscribers — skipping exports.")
            return {}

        logger.info(
            "📤 Dispatching %s to %d exporter(s): %s",
            event,
            len(self._subscribers),
            [s.format for s in self._subscribers],
        )

        results = _run_in_new_loop(self._gather(event), self.timeout_seconds)
        self._log_summary(results)
        return results

    # ------------------------------------------------------------------
    # Dispatch — fire-and-forget
    # ------------------------------------------------------------------

    def publish_background(self, event: SyncCompletedEvent) -> threading.Thread:
        """
        Dispatch event to all subscribers in a background daemon thread.

        Returns immediately; the caller does NOT wait for exports to finish.
        Useful when exports are slow (large Parquet files) and you don't want
        them to block the Dagster pipeline handoff.

        Returns:
            The daemon Thread (join it if you need to wait later).

        Example::

            thread = bus.publish_background(event)
            # pipeline continues here ...
            thread.join(timeout=60)   # optionally wait before process exits
        """
        if not self._subscribers:
            logger.debug("No export subscribers — skipping background export.")
            # Return a no-op thread so callers can always .join() safely.
            t = threading.Thread(target=lambda: None, daemon=True)
            t.start()
            return t

        logger.info(
            "📤 Background export started for %s (%d exporters).",
            event, len(self._subscribers),
        )

        def _run() -> None:
            try:
                results = _run_in_new_loop(self._gather(event), self.timeout_seconds)
                self._log_summary(results)
            except Exception as exc:
                logger.error("Background export failed: %s", exc)

        thread = threading.Thread(target=_run, daemon=True, name="export-bus")
        thread.start()
        return thread

    # ------------------------------------------------------------------
    # Internal async core
    # ------------------------------------------------------------------

    async def _gather(self, event: SyncCompletedEvent) -> Dict[str, ExportResult]:
        """Run all exporter coroutines concurrently."""
        tasks = [exporter.export(event) for exporter in self._subscribers]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: Dict[str, ExportResult] = {}
        for exporter, result in zip(self._subscribers, raw_results):
            if isinstance(result, Exception):
                logger.error(
                    "[%s] Exporter raised an unhandled exception: %s",
                    exporter.format.upper(), result,
                )
                results[exporter.format] = ExportResult(
                    format=exporter.format,
                    failed=1,
                    errors={"__unhandled__": str(result)},
                )
            else:
                results[exporter.format] = result

        return results

    # ------------------------------------------------------------------

    @staticmethod
    def _log_summary(results: Dict[str, ExportResult]) -> None:
        if not results:
            return
        logger.info("📦 Export summary:")
        for fmt, r in results.items():
            icon = "✅" if r.status == "success" else ("⚠️" if r.status == "partial" else "❌")
            logger.info(
                "   %s %s: %d exported, %d failed, %d ms",
                icon, fmt.upper(), r.exported, r.failed, r.duration_ms,
            )
            if r.errors:
                for table, err in r.errors.items():
                    logger.error("      └─ %s: %s", table, err)


# ---------------------------------------------------------------------------
# Helper: run a coroutine in a fresh event loop (safe from sync context)
# ---------------------------------------------------------------------------

def _run_in_new_loop(coro, timeout: Optional[float]):
    """
    Execute `coro` in a new asyncio event loop running in a worker thread.

    This avoids `RuntimeError: This event loop is already running` when
    called from inside Dagster or another framework that has its own loop.
    """
    result_holder: List = []
    error_holder: List = []

    def _thread_target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result_holder.append(loop.run_until_complete(coro))
        except Exception as exc:
            error_holder.append(exc)
        finally:
            loop.close()

    thread = threading.Thread(target=_thread_target, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        raise TimeoutError(
            f"Export did not complete within {timeout}s. "
            "Consider using publish_background() for slow exports."
        )

    if error_holder:
        raise error_holder[0]

    return result_holder[0] if result_holder else {}
