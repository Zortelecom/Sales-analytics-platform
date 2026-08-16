"""Clean Excel files before moving to input folder.

(2026-08) Two fixes.

1. wb.close() moved into a finally block.
   Previously close() ran only on the success path, after wb.save(). If save
   raised -- a full disk, a locked target, a corrupt sheet -- the workbook
   stayed open. On Windows that leaves a handle on the SOURCE file, which is
   what produces the PermissionError the retry loop below then has to work
   around on the next run. The retry loop was treating a symptom of this.

   This is the same fix already applied to BaseExcelExtractor.extract_single_file.

2. batch_preprocess catches unexpected exceptions per file.
   preprocess() only catches (InvalidFileException, IOError, ValueError).
   Anything else -- a zipfile.BadZipFile, a KeyError from a malformed
   workbook -- propagated out of batch_preprocess and killed the whole run.
   One unreadable file should cost you that file, not the batch.
"""
import logging
import re
from pathlib import Path
from time import sleep
from typing import Dict, List, Optional

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

logger = logging.getLogger(__name__)


class ExcelPreprocessor:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.stats = {"processed": 0, "sheets_deleted": 0, "errors": []}

    def preprocess(self, source_file: Path, target_file: Path,
                   delete_pattern: Optional[str] = None,
                   required_sheets: Optional[List[str]] = None) -> bool:
        """
        Copy, strip unwanted sheets, validate, save. True on success.
        """
        wb = None
        try:
            logger.info("Preprocessing %s", source_file.name)

            if self.dry_run:
                logger.info("[DRY RUN] Would process: %s", source_file)
                return True

            for attempt in range(3):
                try:
                    wb = openpyxl.load_workbook(
                        source_file, read_only=False, keep_links=False, data_only=True
                    )
                    break
                except PermissionError as exc:
                    if attempt < 2:
                        logger.warning(
                            "File locked, retrying in 2s... (%s, attempt %d): %s",
                            source_file.name, attempt + 1, exc,
                        )
                        sleep(2)
                    else:
                        raise

            sheets_to_delete = []
            if delete_pattern:
                # "Synthese*" -> "Synthese.*", anchored by match(). Matches both
                # "Synthese Jan" and "SyntheseJan"; a pattern written with a
                # trailing space would only catch the first.
                regex = re.compile(delete_pattern.replace("*", ".*"), re.IGNORECASE)
                sheets_to_delete = [s for s in wb.sheetnames if regex.match(s)]
                if sheets_to_delete:
                    logger.info("  Will delete sheets: %s", sheets_to_delete)

            if required_sheets:
                missing = self._check_required_sheets(wb, required_sheets)
                if missing:
                    raise ValueError(f"Missing required sheets: {missing}")

            if len(sheets_to_delete) == len(wb.sheetnames):
                # openpyxl cannot save a workbook with no sheets, and the error
                # it raises is opaque. Say what actually happened.
                raise ValueError(
                    f"delete_pattern {delete_pattern!r} matches every sheet "
                    f"({wb.sheetnames}) — nothing would be left to extract"
                )

            for sheet_name in sorted(sheets_to_delete, key=wb.sheetnames.index, reverse=True):
                wb.remove(wb[sheet_name])
                self.stats["sheets_deleted"] += 1
                logger.info("  Deleted sheet: %s", sheet_name)

            target_file.parent.mkdir(parents=True, exist_ok=True)
            wb.save(target_file)

            self.stats["processed"] += 1
            logger.info("  Saved to %s", target_file)
            return True

        except (InvalidFileException, IOError, ValueError) as exc:
            message = f"Failed to preprocess {source_file}: {exc}"
            logger.error(message)
            self.stats["errors"].append(message)
            return False

        finally:
            # Always release the handle. On Windows an open workbook holds a
            # lock on the source file that the next run then trips over.
            if wb is not None:
                try:
                    wb.close()
                except Exception:  # noqa: BLE001 — closing must never mask the real error
                    pass

    def _check_required_sheets(self, wb, patterns: List[str]) -> List[str]:
        """A pattern is satisfied if at least one sheet matches it."""
        missing = []
        for pattern in patterns:
            regex = re.compile(pattern.replace("*", ".*"), re.IGNORECASE)
            if not any(regex.match(name) for name in wb.sheetnames):
                missing.append(pattern)
        return missing

    def batch_preprocess(self, manifest: List[Dict]) -> Dict:
        results = {"successful": [], "failed": [], "skipped": []}

        for item in manifest:
            source = item["source_path"]
            target = item["target_dir"] / source.name

            if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
                logger.info("Skipping %s - already up to date", source.name)
                results["skipped"].append(item)
                continue

            try:
                success = self.preprocess(
                    source_file=source,
                    target_file=target,
                    delete_pattern=item["preprocessing"]["delete_sheets"],
                    required_sheets=item["preprocessing"]["required_sheets"],
                )
            except Exception as exc:  # noqa: BLE001 — see module docstring #2
                message = f"Unexpected error preprocessing {source}: {exc}"
                logger.error(message, exc_info=True)
                self.stats["errors"].append(message)
                success = False

            (results["successful"] if success else results["failed"]).append(item)

        return results