"""
Seed Writer Module
Handles writing extracted DataFrames to CSV seeds for SQLMesh
"""
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
import pandas as pd

logger = logging.getLogger(__name__)


class SeedWriter:
    """
    Writes DataFrames to CSV seeds for SQLMesh SEED models.
    Handles metadata tracking and cleanup.
    """

    def __init__(self, seeds_dir: Path, batch_id: str):
        """
        Initialize seed writer.
        
        Args:
            seeds_dir: Directory where CSV seeds will be written
            batch_id: Unique batch identifier for this ingestion run
        """
        self.seeds_dir = Path(seeds_dir)
        self.batch_id = batch_id
        self.timestamp = datetime.now(timezone.utc)

        # Ensure seeds directory exists
        self.seeds_dir.mkdir(parents=True, exist_ok=True)

    def write_seed(
        self,
        df: pd.DataFrame,
        seed_name: str,
        metadata_columns: Optional[List[str]] = None
    ) -> Path:
        """
        Write DataFrame to CSV seed file.
        
        Args:
            df: DataFrame to write
            seed_name: Name of the seed file (without .csv extension)
            metadata_columns: List of internal metadata columns to exclude from seed
                            Default: ['source_file', 'sheet_name', 'table_name', 
                                     'ingestion_ts', 'ingestion_batch_id']
        
        Returns:
            Path to the written CSV file
            
        Raises:
            ValueError: If DataFrame is empty
        """
        if df.empty:
            raise ValueError(
                f"Cannot write empty DataFrame for seed '{seed_name}'")

        # Default metadata columns to exclude
        if metadata_columns is None:
            metadata_columns = [
                'source_file',
                'sheet_name',
                'table_name',
                'ingestion_ts',
                'ingestion_batch_id'
            ]

        # Clean DataFrame: remove internal metadata columns
        df_clean = df.copy()
        columns_to_drop = [
            col for col in metadata_columns if col in df_clean.columns]
        if columns_to_drop:
            df_clean = df_clean.drop(columns=columns_to_drop)
            logger.debug("Dropped metadata columns: %s", columns_to_drop)

        # Write CSV
        seed_path = self.seeds_dir / f"{seed_name}.csv"
        df_clean.to_csv(seed_path, index=False)

        logger.info("✅ Wrote %s rows to %s", len(df_clean), seed_path.name)

        # Write metadata file for tracking
        self._write_metadata(seed_name, df, df_clean)

        return seed_path

    def _write_metadata(
        self,
        seed_name: str,
        df_original: pd.DataFrame,
        df_clean: pd.DataFrame
    ):
        """
        Write metadata file alongside CSV seed for auditing.
        
        Args:
            seed_name: Name of the seed
            df_original: Original DataFrame with metadata
            df_clean: Cleaned DataFrame written to CSV
        """
        metadata_path = self.seeds_dir / f"{seed_name}_metadata.txt"

        # Gather source files if available
        source_files = "N/A"
        if 'source_file' in df_original.columns:
            source_files = df_original['source_file'].unique().tolist()

        # Gather batch IDs if available
        batch_ids = "N/A"
        if 'ingestion_batch_id' in df_original.columns:
            batch_ids = df_original['ingestion_batch_id'].unique().tolist()

        # Write metadata
        with open(metadata_path, 'w', encoding='utf-8') as f:
            f.write("Seed Metadata\n")
            f.write(f"{'='*50}\n\n")
            f.write(f"Seed Name: {seed_name}\n")
            f.write(f"Batch ID: {self.batch_id}\n")
            f.write(f"Timestamp: {self.timestamp.isoformat()}\n")
            f.write(f"Rows Written: {len(df_clean):,}\n")
            f.write(f"Columns: {len(df_clean.columns)}\n")
            f.write("\nColumn Names:\n")
            for col in df_clean.columns:
                f.write(f"  - {col}\n")
            f.write("\nSource Files:\n")
            if isinstance(source_files, list):
                for sf in source_files:
                    f.write(f"  - {sf}\n")
            else:
                f.write(f"  {source_files}\n")
            f.write("\nIngestion Batch IDs:\n")
            if isinstance(batch_ids, list):
                for bid in batch_ids:
                    f.write(f"  - {bid}\n")
            else:
                f.write(f"  {batch_ids}\n")

        logger.debug("Wrote metadata to %s", metadata_path.name)

    def write_seeds(
        self,
        seeds_config: Dict[str, pd.DataFrame]
    ) -> Dict[str, Path]:
        """
        Write multiple seeds at once.
        
        Args:
            seeds_config: Dictionary mapping seed_name -> DataFrame
                         Example: {'sales_data': df_sales, 'targets_data': df_targets}
        
        Returns:
            Dictionary mapping seed_name -> Path to written CSV
            
        Example:
            writer = SeedWriter(Path('sqlmesh/seeds'), 'batch_123')
            paths = writer.write_seeds({
                'sales_data': df_sales,
                'targets_data': df_targets
            })
        """
        written_paths = {}

        for seed_name, df in seeds_config.items():
            if df.empty:
                logger.warning(
                    "Skipping empty DataFrame for seed '%s'", seed_name)
                continue

            try:
                path = self.write_seed(df, seed_name)
                written_paths[seed_name] = path
            except Exception as e:
                logger.error(
                    "Failed to write seed '%s': %s", seed_name, e, exc_info=True)
                # Continue with other seeds instead of failing completely
                continue

        return written_paths

    def cleanup_old_seeds(self, keep_latest: int = 5):
        """
        Clean up old seed metadata files, keeping only the most recent.
        Actual CSV seeds are kept (SQLMesh manages them).
        
        Args:
            keep_latest: Number of latest metadata files to keep per seed
        """
        # Find all metadata files
        metadata_files = sorted(self.seeds_dir.glob("*_metadata.txt"))

        if len(metadata_files) <= keep_latest:
            logger.debug(
                "No cleanup needed: %s metadata files", len(metadata_files))
            return

        # Group by seed name
        seeds_metadata = {}
        for meta_file in metadata_files:
            seed_name = meta_file.stem.replace('_metadata', '')
            if seed_name not in seeds_metadata:
                seeds_metadata[seed_name] = []
            seeds_metadata[seed_name].append(meta_file)

        # Keep only latest N for each seed
        removed_count = 0
        for seed_name, files in seeds_metadata.items():
            if len(files) > keep_latest:
                # Sort by modification time, keep newest
                files_sorted = sorted(
                    files, key=lambda f: f.stat().st_mtime, reverse=True)
                to_remove = files_sorted[keep_latest:]

                for file_to_remove in to_remove:
                    file_to_remove.unlink()
                    removed_count += 1
                    logger.debug(
                        "Removed old metadata: %s", file_to_remove.name)

        if removed_count > 0:
            logger.info("Cleaned up %s old metadata file(s)", removed_count)

    def get_seed_summary(self) -> Dict[str, Dict]:
        """
        Get summary of all seeds in the directory.
        
        Returns:
            Dictionary mapping seed_name -> {path, size, rows, modified}
        """
        summary = {}

        for csv_file in self.seeds_dir.glob("*.csv"):
            seed_name = csv_file.stem

            # Get file stats
            stats = csv_file.stat()

            # Count rows (quick estimate - header + data rows)
            try:
                with open(csv_file, 'r', encoding='utf-8') as f:
                    row_count = sum(1 for _ in f) - 1  # Exclude header
            except FileNotFoundError:
                print(f"Error: File {csv_file} not found.")
                row_count = None
            except OSError as e:
                print(f"Error reading file {csv_file}: {e}")
                row_count = None

            summary[seed_name] = {
                'path': csv_file,
                'size_mb': stats.st_size / (1024 * 1024),
                'rows': row_count,
                'modified': datetime.fromtimestamp(stats.st_mtime)
            }

        return summary
