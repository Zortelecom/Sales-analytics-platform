"""Manage archiving of processed source files"""
import logging
import shutil
from pathlib import Path
from datetime import datetime
from typing import List, Dict

logger = logging.getLogger(__name__)


class ArchiveManager:
    def __init__(self, archive_base: Path):
        self.archive_base = archive_base
        self.batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.batch_archive_path = archive_base / f"batch_{self.batch_id}"

    def archive_processed_files(self, manifest: List[Dict],
                                move: bool = False) -> Path:
        """
        Archive or copy processed source files
        
        Args:
            manifest: List of processed file metadata
            move: If True, move files; if False, copy files
        """
        self.batch_archive_path.mkdir(parents=True, exist_ok=True)

        for item in manifest:
            source = item["source_path"]
            dest = self.batch_archive_path / source.name

            try:
                if move:
                    shutil.move(str(source), str(dest))
                    logger.info("Moved to archive: %s", source.name)
                else:
                    shutil.copy2(str(source), str(dest))
                    logger.info("Copied to archive: %s", source.name)
            except (OSError, IOError) as e:
                logger.error("Failed to archive %s: %s", source.name, e)

        # Write manifest
        manifest_file = self.batch_archive_path / "_manifest.txt"
        with open(manifest_file, "w", encoding="utf-8") as f:
            f.write(f"Batch: {self.batch_id}\n")
            f.write(f"Timestamp: {datetime.now().isoformat()}\n")
            f.write(f"Files: {len(manifest)}\n\n")
            for item in manifest:
                f.write(
                    f"- {item['source_path'].name} ({item['source_type']})\n")

        return self.batch_archive_path
