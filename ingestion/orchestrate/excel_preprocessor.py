"""Clean Excel files before moving to input folder"""
import logging
import re
from pathlib import Path
from time import time, sleep
from typing import Dict, List, Optional
import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

logger = logging.getLogger(__name__)


class ExcelPreprocessor:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.stats = {
            "processed": 0,
            "sheets_deleted": 0,
            "errors": []
        }

    def preprocess(self, source_file: Path, target_file: Path,
                   delete_pattern: Optional[str] = None,
                   required_sheets: Optional[List[str]] = None) -> bool:
        """
        Preprocess Excel file:
        1. Copy to temp location
        2. Delete sheets matching pattern
        3. Validate required sheets exist
        4. Save to target

        Returns: True if successful, False otherwise
        """
        try:
            logger.info("Preprocessing %s", source_file.name)

            if self.dry_run:
                logger.info("[DRY RUN] Would process: %s", source_file)
                return True

            # Load workbook
            for attempt in range(3):
                try:
                    wb = openpyxl.load_workbook(
                        source_file, read_only=False, keep_links=False, data_only=True)
                    break
                except PermissionError as e:
                    if attempt < 2:
                        logger.warning("File locked, Retrying in 2s... (%s, attempt %d): %s",
                                       source_file.name, attempt + 1, e)
                        sleep(2)  # Wait before retrying
                    else:
                        raise

            # Track sheets to delete
            sheets_to_delete = []
            if delete_pattern:
                regex = re.compile(delete_pattern.replace(
                    "*", ".*"), re.IGNORECASE)
                sheets_to_delete = [
                    sheet for sheet in wb.sheetnames
                    if regex.match(sheet)
                ]

                if sheets_to_delete:
                    logger.info("  Will delete sheets: %s", sheets_to_delete)

            # Validate required sheets
            if required_sheets:
                missing = self._check_required_sheets(wb, required_sheets)
                if missing:
                    raise ValueError(f"Missing required sheets: {missing}")

            # Delete sheets (in reverse order to maintain indices)
            for sheet_name in sorted(sheets_to_delete, key=wb.sheetnames.index, reverse=True):
                std = wb[sheet_name]
                wb.remove(std)
                self.stats["sheets_deleted"] += 1
                logger.info("  Deleted sheet: %s", sheet_name)

            # Ensure target directory exists
            target_file.parent.mkdir(parents=True, exist_ok=True)

            # Save cleaned file
            wb.save(target_file)
            wb.close()

            self.stats["processed"] += 1
            logger.info("  ✓ Saved to %s", target_file)
            return True

        except (InvalidFileException, IOError, ValueError) as e:
            error_msg = f"Failed to preprocess {source_file}: {e}"
            logger.error(error_msg)
            self.stats["errors"].append(error_msg)
            return False

    def _check_required_sheets(self, wb, patterns: List[str]) -> List[str]:
        """Check if at least one sheet matches each required pattern"""
        missing = []
        for pattern in patterns:
            regex = re.compile(pattern.replace("*", ".*"), re.IGNORECASE)
            if not any(regex.match(name) for name in wb.sheetnames):
                missing.append(pattern)
        return missing

    def batch_preprocess(self, manifest: List[Dict]) -> Dict:
        """Process multiple files from manifest"""
        results = {
            "successful": [],
            "failed": [],
            "skipped": []
        }

        for item in manifest:
            source = item["source_path"]
            target = item["target_dir"] / source.name

            # Check if target already exists and is newer
            if target.exists():
                if target.stat().st_mtime >= source.stat().st_mtime:
                    logger.info(
                        "Skipping %s - already up to date", source.name)
                    results["skipped"].append(item)
                    continue

            success = self.preprocess(
                source_file=source,
                target_file=target,
                delete_pattern=item["preprocessing"]["delete_sheets"],
                required_sheets=item["preprocessing"]["required_sheets"]
            )

            if success:
                results["successful"].append(item)
            else:
                results["failed"].append(item)

        return results
