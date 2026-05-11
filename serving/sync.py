#!/usr/bin/env python3
"""
DuckLake to Serving Layer Sync
Handles physical table materialization with atomic swaps and robust error handling

Features:
- Atomic file swaps for zero-downtime deployment
- Automatic DuckLake extension installation
- Batched processing for large tables
- Comprehensive error handling and retry logic
- Detailed sync metadata tracking
- Partial sync tolerance
- Dual-format export: Parquet (analytics) and CSV (compatibility)
"""

import shutil
import logging
from pathlib import Path
from datetime import datetime
from time import sleep
from typing import List, Tuple, Optional, Dict, Literal
import duckdb
from .config import ServingConfig

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ServingLayerSync:
    """
    Syncs DuckLake marts tables to a serving DuckDB database for BI tools.

    Design Principles:
    - Read-only access to DuckLake (source of truth)
    - Atomic swaps prevent partial state exposure
    - Failure tolerance: partial syncs can proceed if <50% tables fail
    - Memory efficiency: large tables are batched
    - Dual-format exports: Parquet for analytics, CSV for compatibility
    """

    def __init__(self, config: Optional[ServingConfig] = None):
        self.config = config or ServingConfig()

        if not self.config.marts_schema:
            raise ValueError(
                "ServingConfig.normalize() must be called before creating ServingLayerSync"
            )

        self.sync_metadata = []
        self.failed_tables = []
        self.batch_size = 100_000  # Rows per batch for large tables

    def _ensure_ducklake_extension(self, conn: duckdb.DuckDBPyConnection) -> None:
        """
        Ensure DuckLake extension is installed and loaded.
        Auto-installs from community repository if not present.
        """
        try:
            conn.execute("LOAD ducklake;")
            logger.debug("DuckLake extension loaded")
        except duckdb.CatalogException:
            logger.warning(
                "DuckLake extension not found, installing from community...")
            try:
                conn.execute("INSTALL ducklake;")
                conn.execute("LOAD ducklake;")
                logger.info("✅ DuckLake extension installed and loaded")
            except Exception as e:
                raise RuntimeError(
                    f"Failed to install DuckLake extension: {e}. "
                    "Try manually: duckdb -c \"INSTALL ducklake;\""
                ) from e

    def discover_marts_tables(self, conn: duckdb.DuckDBPyConnection) -> List[Tuple[str, str]]:
        """
        Discover all tables in the marts schema.

        Returns:
            List of (schema, table_name) tuples
        """
        
        catalog = self.config.source_catalog_alias
        
        query = """
            SELECT table_schema, table_name 
            FROM information_schema.tables 
            WHERE table_schema = ?
              -- AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """
        try:
            result = conn.execute(query, [self.config.marts_schema]).fetchall()

            if not result:
                logger.warning(
                    "⚠️ No table found in %s.%s", catalog, self.config.marts_schema)
                # Listons ce qui existe vraiment pour comprendre
                all_schemas = conn.execute(
                    f"SELECT DISTINCT table_schema FROM {catalog}.information_schema.tables").fetchall()
                logger.info(
                    "ℹ️ Schemas available in the catalog : %s", [s[0] for s in all_schemas])

            logger.info(
                "Discovered %d tables in schema '%s'", len(result), self.config.marts_schema)
            return result
        except duckdb.CatalogException:
            logger.error(
                "Failed to query schema '%s'", self.config.marts_schema)
            return []

    def get_table_stats(
        self,
        conn: duckdb.DuckDBPyConnection,
        schema: str,
        table: str,
        catalog: Optional[str] = None
    ) -> Dict[str, any]:
        """
        Get row count and column info for validation.

        Args:
            conn: DuckDB connection
            schema: Schema name
            table: Table name
            catalog: Optional catalog prefix (e.g., 'sales_lake')

        Returns:
            Dictionary with row_count, column_count, status
        """
        try:
            full_name = f"{catalog}.{schema}.{table}" if catalog else f"{schema}.{table}"

            # Get row count
            count_result = conn.execute(
                f"SELECT COUNT(*) FROM {full_name}").fetchone()
            row_count = count_result[0] if count_result else 0

            # Get column count
            col_result = conn.execute(
                """ SELECT COUNT(*) 
                FROM information_schema.columns 
                WHERE table_schema = ? AND table_name = ?
            """, [schema, table]).fetchone()
            col_count = col_result[0] if col_result else 0

            return {
                "row_count": row_count,
                "column_count": col_count,
                "status": "success"
            }
        except (duckdb.CatalogException, duckdb.IOException, TypeError) as e:
            logger.warning("Failed to get stats for %s.%s: %s",
                           schema, table, e)
            return {
                "row_count": 0,
                "column_count": 0,
                "status": "error",
                "error": str(e)
            }

    def sync(self, dry_run: bool = False) -> Dict[str, any]:
        """
        Sync marts tables to serving database.

        Strategy:
        1. Connect to DuckLake (read-only via ATTACH)
        2. Create temporary serving database
        3. Copy tables (with batching for large tables)
        4. Atomic file swap
        5. Cleanup

        Args:
            dry_run: If True, only report what would be synced

        Returns:
            Dictionary with sync summary statistics
        """
        logger.info("="*70)
        logger.info(
            "Starting serving sync | env=%s | schema=%s",
            self.config.environment,
            self.config.marts_schema
        )
        logger.info("="*70)

        if dry_run:
            logger.info("🔍 DRY RUN MODE: No changes will be made")

        # Validate source exists
        ducklake_path = Path(self.config.ducklake_path)
        if not ducklake_path.exists():
            raise FileNotFoundError(
                f"DuckLake catalog not found: {self.config.ducklake_path}\n"
                f"Run SQLMesh transformations first to create the catalog."
            )

        # Step 1: Source connection (in-memory with DuckLake attached)
        source = duckdb.connect(":memory:")

        try:
            # Load DuckLake extension
            self._ensure_ducklake_extension(source)

            # Attach the catalog
            catalog_alias = getattr(
                self.config, 'source_catalog_alias', 'sales_lakehouse')
            logger.info("Attaching DuckLake catalog as '%s'...", catalog_alias)

            source.execute(
                f"ATTACH '{self.config.ducklake_path}' AS {self.config.source_catalog_alias} (TYPE DUCKLAKE)"
            )

            # Validate source schema exists
            self._validate_source_schema(source)

            # Discover tables
            tables = self.discover_marts_tables(source)

            if not tables:
                raise ValueError(
                    f"No tables found in schema '{self.config.marts_schema}'. "
                    f"Have you run SQLMesh transformations?"
                )

            logger.info(
                "Found %i table(s): %s", len(tables), [t[1] for t in tables])

            # DRY RUN: Just report what would be synced
            if dry_run:
                return self._dry_run_report(source, tables, catalog_alias)

            # Step 2: Prepare temp database
            temp_path = Path(self.config.temp_path)
            if temp_path.exists():
                logger.info(
                    "Removing existing temp database: %s", temp_path)
                temp_path.unlink()

            logger.info("Creating temp serving database: %s", temp_path)
            target = duckdb.connect(str(temp_path))
            try:
                # Step 3: Setup BI schema and metadata tracking
                self._setup_serving_schema(target)

                # Step 4: Sync tables
                sync_start = datetime.now()
                self._sync_all_tables(source, target, tables, catalog_alias)
                
                 # Step 5: Apply BI views (if configured)
                if self.config.apply_views:
                    self._apply_bi_views(target)

                # Step 6: Atomic swap if success rate is acceptable
                success_count = len(tables) - len(self.failed_tables)
                failure_rate = len(self.failed_tables) / \
                    len(tables) if tables else 0

                if failure_rate >= 0.5:  # 50% or more failed
                    error_msg = (
                        f"Sync aborted: {len(self.failed_tables)}/{len(tables)} tables failed. "
                        f"Errors:\n" + "\n".join(
                            f"  - {t}: {e}" for t, e in self.failed_tables
                        )
                    )
                    raise RuntimeError(error_msg)

                if self.failed_tables:
                    logger.warning(
                        "Proceeding with partial sync: %i/%i tables succeeded", success_count, len(tables)
                    )

                # Close target before swap
                target.close()

                # Perform atomic swap
                self._atomic_swap()
                
                # Export to external formats if configured
                export_results = self._export_to_external_formats(tables)

                duration = (datetime.now() - sync_start).total_seconds()

                # Summary
                summary = {
                    "status": "success" if not self.failed_tables else "partial_success",
                    "duration_seconds": duration,
                    "tables_total": len(tables),
                    "tables_succeeded": success_count,
                    "tables_failed": len(self.failed_tables),
                    "failed_tables": [t for t, _ in self.failed_tables],
                    "serving_path": self.config.serving_path,
                    "exports": export_results
                }

                logger.info("="*70)
                logger.info("✅ Sync completed in %.2fs", duration)
                logger.info(
                    "   Success: %i/%i tables", success_count, len(tables))
                if self.failed_tables:
                    logger.warning(
                        "   Failed: %i tables", len(self.failed_tables))
                if export_results:
                    logger.info("   Exports: %s", export_results)
                logger.info("   Serving database: %s", self.config.serving_path)
                logger.info("="*70)

                return summary

            except Exception as e:
                # Clean up temp database on error
                target.close()
                if temp_path.exists():
                    temp_path.unlink()
                    logger.info("Cleaned up temp database after error")
                raise

        finally:
            source.close()

    def _dry_run_report(
        self,
        source: duckdb.DuckDBPyConnection,
        tables: List[Tuple[str, str]],
        catalog_alias: str
    ) -> Dict[str, any]:
        """Generate dry-run report without making changes"""
        logger.info("\n" + "="*70)
        logger.info("DRY RUN REPORT")
        logger.info("="*70)

        total_rows = 0
        for schema, table in tables:
            if self._should_skip_table(table):
                logger.info("⏭️  SKIP: %s (excluded by config)", table)
                continue

            stats = self.get_table_stats(
                source, schema, table, catalog=catalog_alias)
            total_rows += stats['row_count']

            status_icon = "✅" if stats['status'] == 'success' else "❌"
            logger.info(
                "%s Would sync: %s (%i rows, %i cols)", status_icon, table, stats['row_count'], stats['column_count']
            )

        logger.info("="*70)
        logger.info("Total: %i tables, %i rows", len(tables), total_rows)
        logger.info("="*70)

        return {
            "status": "dry_run",
            "tables_count": len(tables),
            "total_rows": total_rows,
        }

    def _setup_serving_schema(self, target: duckdb.DuckDBPyConnection) -> None:
        """Create BI schema and metadata tracking table"""
        logger.info(
            "Setting up schema '%s' in serving database...", self.config.bi_schema)

        target.execute("CREATE SCHEMA IF NOT EXISTS " + self.config.bi_schema)

        # Create metadata tracking table
        target.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.config.bi_schema}._sync_log (
                sync_timestamp TIMESTAMP NOT NULL,
                source_table VARCHAR NOT NULL,
                target_table VARCHAR NOT NULL,
                row_count BIGINT NOT NULL,
                duration_ms BIGINT NOT NULL,
                status VARCHAR NOT NULL,
                error_message VARCHAR
            )
        """)

        logger.debug("Metadata tracking table created")

    def _sync_all_tables(
        self,
        source: duckdb.DuckDBPyConnection,
        target: duckdb.DuckDBPyConnection,
        tables: List[Tuple[str, str]],
        catalog_alias: str
    ) -> None:
        """Sync all tables from source to target"""

        # Setup export directories if enabled
        self._setup_export_directories()

        logger.info("Syncing %d table(s)...", len(tables))

        for idx, (schema, table) in enumerate(tables, 1):
            logger.info("Processing table %d/%d: %s", idx, len(tables), table)

            if self._should_skip_table(table):
                logger.info("  ⏭️  Skipped (excluded by config)")
                continue

            try:
                # Check if table should be batched
                stats = self.get_table_stats(
                    source, schema, table, catalog=catalog_alias)
                row_count = stats.get('row_count', 0)

                if row_count > self.batch_size:
                    self._sync_table_batched(
                        source, target, schema, table, catalog_alias)
                else:
                    self._sync_table(source, target, schema,
                                     table, catalog_alias)

            except Exception as e:
                logger.error("  ❌ Failed to sync %s: %s", table, e)
                self.failed_tables.append((table, str(e)))

                # Log failure to metadata
                self._log_sync_failure(target, schema, table, str(e))

                # Continue with next table
                self._log_sync_failure(target, schema, table, str(e))
                continue

    def _setup_export_directories(self) -> None:
        """Setup export directories for CSV and Parquet files."""
        # CSV export directory
        if self.config.enable_csv_export and self.config.csv_export_path:
            csv_dir = Path(self.config.csv_export_path)
            if csv_dir.exists():
                logger.info("Cleaning CSV export directory: %s", csv_dir)
                shutil.rmtree(csv_dir)
            csv_dir.mkdir(parents=True, exist_ok=True)
            logger.info("📁 CSV export directory: %s", csv_dir)

        # Parquet export directory
        if self.config.enable_parquet_export and self.config.parquet_export_path:
            parquet_dir = Path(self.config.parquet_export_path)
            if parquet_dir.exists():
                logger.info("Cleaning Parquet export directory: %s", parquet_dir)
                shutil.rmtree(parquet_dir)
            parquet_dir.mkdir(parents=True, exist_ok=True)
            logger.info("📁 Parquet export directory: %s", parquet_dir)

    def _should_skip_table(self, table: str) -> bool:
        """Check if table should be excluded from sync"""
        if self.config.include_tables:
            return table not in self.config.include_tables
        if self.config.exclude_tables:
            return table in self.config.exclude_tables
        return False

    def _sync_table(
        self,
        source: duckdb.DuckDBPyConnection,
        target: duckdb.DuckDBPyConnection,
        source_schema: str,
        table: str,
        catalog_alias: str
    ) -> None:
        """
        Sync individual table using CREATE TABLE AS SELECT.
        For small to medium tables (<100k rows).
        """
        start = datetime.now()
        source_full = f"{catalog_alias}.{source_schema}.{table}"
        target_table = f"{self.config.bi_schema}.{table}"

        logger.info("  Syncing %s → %s", source_full, target_table)
        
        df = source.execute(
            f"SELECT * FROM {source_full}"
        ).fetch_df()
        
        target.register("df_tmp", df)

        target.execute(f"""
            CREATE TABLE {target_table} AS
            SELECT * FROM df_tmp
        """)
        target.unregister("df_tmp")
        # Verify and get stats
        row_count = target.execute(
            f"SELECT COUNT(*) FROM {target_table}"
        ).fetchone()[0]

        duration = int((datetime.now() - start).total_seconds() * 1000)

        # Log metadata
        target.execute(f"""
            INSERT INTO {self.config.bi_schema}._sync_log
            (sync_timestamp, source_table, target_table, row_count, duration_ms, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [datetime.now(), f"{source_schema}.{table}", target_table, row_count, duration, "success"])

        logger.info("  ✅ %i rows in %dms", row_count, duration)

    def _sync_table_batched(
        self,
        source: duckdb.DuckDBPyConnection,
        target: duckdb.DuckDBPyConnection,
        source_schema: str,
        table: str,
        catalog_alias: str
    ) -> None:
        """
        Sync large table in batches to control memory usage.
        For tables >100k rows.
        """
        start = datetime.now()
        full_table = f"{source_schema}.{table}"
        source_full = f"{catalog_alias}.{full_table}"
        target_table = f"{self.config.bi_schema}.{table}"

        # Get total row count
        total = source.execute(
            f"SELECT COUNT(*) FROM {source_full}"
        ).fetchone()[0]

        logger.info(
            "Large table detected (%s rows), using batched copy (%s rows/batch)...",
            f"{total:,}",
            f"{self.batch_size:,}"
        )

        # Create empty table with same schema
        target.execute(
            f"CREATE TABLE {target_table} AS SELECT * FROM {source_full} LIMIT 0"
        )

        # Insert in batches
        batches = (total + self.batch_size - 1) // self.batch_size
        for batch_num in range(batches):
            offset = batch_num * self.batch_size
            limit = min(self.batch_size, total - offset)

            target.execute(f"""
                INSERT INTO {target_table}
                SELECT * FROM {source_full}
                LIMIT {limit} OFFSET {offset}
            """)

            logger.info(
                "    Batch %s/%s: rows %s - %s",
                batch_num + 1,
                batches,
                f"{offset:,}",
                f"{offset + limit:,}"
            )

        duration = int((datetime.now() - start).total_seconds() * 1000)

        # Verify final count
        final_count = target.execute(
            f"SELECT COUNT(*) FROM {target_table}"
        ).fetchone()[0]

        if final_count != total:
            raise ValueError(
                f"Row count mismatch: expected {total:,}, got {final_count:,}"
            )

        # Log metadata
        target.execute(f"""
            INSERT INTO {self.config.bi_schema}._sync_log
            (sync_timestamp, source_table, target_table, row_count, duration_ms, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [datetime.now(), full_table, target_table, final_count, duration, "success"])

        logger.info(
            "  ✅ %i rows in %dms (batched)", f"{final_count:,}", duration)

    def _log_sync_failure(
        self,
        target: duckdb.DuckDBPyConnection,
        schema: str,
        table: str,
        error: str
    ) -> None:
        """Log sync failure to metadata table"""
        try:
            target.execute(f"""
                INSERT INTO {self.config.bi_schema}._sync_log 
                (sync_timestamp, source_table, target_table, row_count, duration_ms, status, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [
                datetime.now(),
                f"{schema}.{table}",
                f"{self.config.bi_schema}.{table}",
                0,
                0,
                "error",
                error[:500]  # Truncate long errors
            ])
        except Exception as e:
            logger.error("Failed to log error for %s: %s", table, e)

    def _atomic_swap(self) -> None:
        """
        Perform atomic file swap for zero-downtime deployment.

        Strategy:
        1. Move current serving DB to backup (if exists)
        2. Move temp DB to serving location
        3. Clean up backup (optional: keep for rollback)
        """
        serving_path = Path(self.config.serving_path)
        temp_path = Path(self.config.temp_path)
        backup_path = Path(
            self.config.serving_path.replace(".db", "_backup.db"))

        logger.info("\nPerforming atomic swap...")

        # Step 1: Backup existing serving DB
        if serving_path.exists():
            if backup_path.exists():
                logger.debug("Removing old backup: %s", backup_path)
                backup_path.unlink()

            logger.info("  Backing up: %s → %s", serving_path, backup_path)
            shutil.move(str(serving_path), str(backup_path))

        # Step 2: Move temp to serving location (with retry for Windows)
        logger.info("  Activating: %s → %s", temp_path, serving_path)

        for attempt in range(3):
            try:
                shutil.move(str(temp_path), str(serving_path))
                break
            except PermissionError as e:
                if attempt < 2:
                    logger.warning(
                        "File locked, retrying in 0.5s... (attempt %i/3)", attempt + 1)
                    sleep(0.5)
                else:
                    # Restore backup on final failure
                    if backup_path.exists():
                        shutil.move(str(backup_path), str(serving_path))
                        logger.error("Restored backup after swap failure")
                    raise RuntimeError(
                        f"Failed to swap files after 3 attempts: {e}") from e

        # Step 3: Clean up backup (optional: comment out to keep for rollback)
        if backup_path.exists():
            backup_path.unlink()
            logger.debug("Cleaned up backup file")

        logger.info("  ✅ Swap complete: %s", serving_path)

    def _export_to_external_formats(
        self,
        tables: List[Tuple[str, str]]
    ) -> Dict[str, any]:
        """
        Export synced tables to external formats (CSV and Parquet).
        
        This runs after the atomic swap, exporting from the new serving DB.
        
        Args:
            tables: List of (schema, table_name) tuples that were synced
            
        Returns:
            Dictionary with export statistics
        """
        if not self.config.enable_csv_export and not self.config.enable_parquet_export:
            return {}
        
        serving_path = Path(self.config.serving_path)
        if not serving_path.exists():
            logger.warning("Serving DB not found for export, skipping")
            return {"status": "skipped", "reason": "serving_db_not_found"}
        
        export_results = {
            "csv": {"exported": 0, "failed": 0, "path": str(self.config.csv_export_path) if self.config.csv_export_path else None},
            "parquet": {"exported": 0, "failed": 0, "path": str(self.config.parquet_export_path) if self.config.parquet_export_path else None}
        }
        
        conn = None
        try:
            conn = duckdb.connect(str(serving_path), read_only=True)
            
            for schema, table in tables:
                if self._should_skip_table(table):
                    continue
                
                # Export to CSV
                if self.config.enable_csv_export and self.config.csv_export_path:
                    try:
                        csv_path = Path(self.config.csv_export_path) / f"{table}.csv"
                        self._export_single_table(
                            conn, table, csv_path, "csv"
                        )
                        export_results["csv"]["exported"] += 1
                    except Exception as e:
                        logger.error("  ❌ Failed to export CSV for %s: %s", table, e)
                        export_results["csv"]["failed"] += 1
                
                # Export to Parquet
                if self.config.enable_parquet_export and self.config.parquet_export_path:
                    try:
                        parquet_path = Path(self.config.parquet_export_path) / f"{table}.parquet"
                        self._export_single_table(
                            conn, table, parquet_path, "parquet"
                        )
                        export_results["parquet"]["exported"] += 1
                    except Exception as e:
                        logger.error("  ❌ Failed to export Parquet for %s: %s", table, e)
                        export_results["parquet"]["failed"] += 1
            
            export_results["status"] = "success"
            
        except Exception as e:
            logger.error("Failed to export to external formats: %s", e)
            export_results["status"] = "error"
            export_results["error"] = str(e)
        finally:
            if conn:
                conn.close()
        
        return export_results

    def _export_single_table(
        self,
        conn: duckdb.DuckDBPyConnection,
        table_name: str,
        target_path: Path,
        format_type: Literal["csv", "parquet"]
    ) -> None:
        """
        Export a single table to CSV or Parquet format.
        
        Args:
            conn: DuckDB connection to serving database
            table_name: Name of the table to export
            target_path: Destination file path
            format_type: Either "csv" or "parquet"
        """
        # Ensure parent directory exists
        target_path.parent.mkdir(parents=True, exist_ok=True)
        
        full_table_name = f"{self.config.bi_schema}.{table_name}"
        
        if format_type == "csv":
            logger.info("    📄 Exporting CSV: %s", target_path.name)
            conn.execute(f"""
                COPY {full_table_name} 
                TO '{str(target_path)}' 
                (HEADER, DELIMITER ',', QUOTE '"')
            """)
        elif format_type == "parquet":
            logger.info("    🦆 Exporting Parquet: %s", target_path.name)
            # Use Snappy compression by default for good balance of speed/compression
            conn.execute(f"""
                COPY {full_table_name} 
                TO '{str(target_path)}' 
                (FORMAT PARQUET, COMPRESSION 'snappy')
            """)
        else:
            raise ValueError(f"Unsupported export format: {format_type}")

    def validate_serving_db(self) -> bool:
        """
        Validate that serving DB is queryable and recent.

        Checks:
        - File exists
        - Can connect and query
        - Last sync was within 24 hours

        Returns:
            True if valid, False otherwise
        """
        serving_path = Path(self.config.serving_path)

        if not serving_path.exists():
            logger.warning("Serving database not found: %s", serving_path)
            return False

        try:
            conn = duckdb.connect(str(serving_path), read_only=True)

            # Check for sync log table
            tables = conn.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = ? AND table_name = '_sync_log'
            """, [self.config.bi_schema]).fetchall()

            if not tables:
                logger.warning("Sync log table not found in serving DB")
                conn.close()
                return False

            # Check last sync time
            result = conn.execute(f"""
                SELECT MAX(sync_timestamp) FROM {self.config.bi_schema}._sync_log
            """).fetchone()

            conn.close()

            if result and result[0]:
                last_sync = result[0]
                age_seconds = (datetime.now() - last_sync).total_seconds()
                age_hours = age_seconds / 3600

                logger.info(
                    "Serving DB last synced: %.1f hours ago (%s)",
                    age_hours,
                    last_sync.strftime('%Y-%m-%d %H:%M:%S')
                )
                # Consider fresh if less than 24 hours old
                return age_seconds < 86400
            else:
                logger.warning("No sync records found in serving DB")
                return False

        except (duckdb.CatalogException, duckdb.IOException, TypeError) as e:
            logger.error("Validation error: %s", e)
            return False

    def _validate_source_schema(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Validate that the source schema exists in DuckLake catalog"""
        schemas = conn.execute("""
            SELECT schema_name 
            FROM information_schema.schemata
        """).fetchall()

        schema_names = {s[0] for s in schemas}

        if self.config.marts_schema not in schema_names:
            available = ", ".join(sorted(schema_names))
            raise ValueError(
                f"Schema '{self.config.marts_schema}' not found in DuckLake catalog.\n"
                f"Environment: {self.config.environment}\n"
                f"Available schemas: {available}\n\n"
                f"Hint: Run SQLMesh transformations first to create marts tables."
            )

        logger.debug("Validated source schema: %s", self.config.marts_schema)

    def get_sync_stats(self) -> Optional[Dict[str, any]]:
        """
        Get statistics from the most recent sync.

        Returns:
            Dictionary with sync statistics or None if serving DB doesn't exist
        """
        serving_path = Path(self.config.serving_path)

        if not serving_path.exists():
            return None

        try:
            conn = duckdb.connect(str(serving_path), read_only=True)

            # Get overall stats
            result = conn.execute(f"""
                SELECT 
                    MAX(sync_timestamp) as last_sync,
                    COUNT(*) as total_syncs,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful_syncs,
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as failed_syncs,
                    SUM(row_count) as total_rows,
                    AVG(duration_ms) as avg_duration_ms
                FROM {self.config.bi_schema}._sync_log
            """).fetchone()

            conn.close()

            if result:
                return {
                    "last_sync": result[0],
                    "total_syncs": result[1],
                    "successful_syncs": result[2],
                    "failed_syncs": result[3],
                    "total_rows": result[4],
                    "avg_duration_ms": result[5],
                }

            return None

        except Exception as e:
            logger.error("Failed to get sync stats: %s", e)
            return None

    def _apply_bi_views(self, target: duckdb.DuckDBPyConnection) -> None:
            """
            Execute the BI view definitions on the target database.
            Raises an exception if any view creation fails (atomic sync will abort).
            """
            sql_path = Path(self.config.views_template_path)
            if not sql_path.exists():
                raise FileNotFoundError(f"BI views template not found: {sql_path}")

            logger.info("Applying BI views from %s", sql_path)
            sql_content = sql_path.read_text(encoding="utf-8")

            # Split on semicolons to handle multi‑statement files safely
            # This simple split works for typical view definitions without semicolons inside strings.
            statements = [stmt.strip() for stmt in sql_content.split(";") if stmt.strip()]

            created_views = []
            for stmt in statements:
                try:
                    target.execute(stmt)
                    # Extract view name from CREATE VIEW statement (simple heuristic)
                    if stmt.upper().startswith("CREATE OR REPLACE VIEW"):
                        parts = stmt.split()
                        # The view name is usually after "VIEW"
                        view_name = parts[3] if len(parts) > 3 else "unknown"
                        created_views.append(view_name)
                    logger.debug("Executed: %s...", stmt[:60])
                except Exception as e:
                    logger.error("Failed to execute view statement: %s", stmt[:100])
                    raise RuntimeError(f"BI view creation failed: {e}") from e

            logger.info("✅ Created %d BI view(s)", len(created_views))



