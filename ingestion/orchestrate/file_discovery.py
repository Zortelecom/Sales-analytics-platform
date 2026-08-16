"""Discover and validate source files from the SharePoint/local sync folder.

(2026-08) Driven by the source registry instead of a hardcoded list.

The four source types were spelled out three times in this file -- in
__init__, in discover_all, and implicitly via getattr(config, f"{t}_path").
Adding a source meant editing all three plus SourceConfig. They now come from
sources.yaml, like everything else.

Validation limits (max size, allowed extensions) now come from sources.yaml's
`validation:` block, which previously nothing read while this file carried its
own hardcoded copies.
"""
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING

from shared.sources import load_sources

if TYPE_CHECKING:
    from ingestion.config.settings import SourceConfig

logger = logging.getLogger(__name__)


class FileDiscovery:
    def __init__(self, config: "SourceConfig"):
        self.config = config
        self.registry = load_sources()
        self.discovered_files: Dict[str, List[Path]] = {
            source_type: [] for source_type in self.registry.source_types
        }

    def discover_all(self) -> Dict[str, List[Path]]:
        """
        Scan every declared source directory.

        Must be called before check_for_new_files() or
        get_processing_manifest() -- both read self.discovered_files.
        """
        for source_type, spec in self.registry.sources.items():
            if not spec.source_dir.exists():
                logger.warning("Source path does not exist: %s", spec.source_dir)
                continue

            files = self._scan_directory(spec.source_dir, spec.file_pattern)
            self.discovered_files[source_type] = files

            logger.info("Discovered %d %s file(s) matching %s",
                        len(files), source_type, spec.file_pattern)
            for path in files:
                logger.info("  - %s", path.name)

        return self.discovered_files

    def _scan_directory(self, path: Path, pattern: str) -> List[Path]:
        valid = []
        for candidate in path.glob(pattern):
            if candidate.name.startswith("~$"):
                continue  # Excel lock file
            if self._validate_file(candidate):
                valid.append(candidate)
            else:
                logger.warning("Skipping invalid file: %s", candidate)

        valid.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return valid

    def _validate_file(self, file: Path) -> bool:
        rules = self.registry.validation
        suffix = file.suffix.lower()

        if suffix == ".xls":
            logger.error("Cannot process .xls file: %s (re-save as .xlsx)", file.name)
            return False
        if suffix not in {e.lower() for e in rules.allowed_extensions}:
            return False

        try:
            size_mb = file.stat().st_size / (1024 * 1024)
        except OSError:
            return False
        if size_mb > rules.max_file_size_mb:
            logger.warning("File too large: %s (%.1fMB > %dMB)",
                           file.name, size_mb, rules.max_file_size_mb)
            return False

        return True

    def check_for_new_files(
        self, since: Optional[datetime] = None
    ) -> Dict[str, List[Path]]:
        """
        Filter to files modified since `since`.

        Defaults to the LAST 24 HOURS. That is right for a scheduled run and
        wrong for an initial load -- pass an explicit `since` (ingestion.main
        exposes --all and --since for exactly this).
        """
        if since is None:
            since = datetime.now(timezone.utc) - timedelta(hours=24)
        elif since.tzinfo is None:
            # mtimes below are tz-aware; comparing them against a naive
            # datetime raises TypeError. Assume UTC rather than blow up.
            logger.warning("Naive `since` received, assuming UTC: %s", since)
            since = since.replace(tzinfo=timezone.utc)

        return {
            source_type: [
                path for path in files
                if datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) > since
            ]
            for source_type, files in self.discovered_files.items()
        }

    def get_processing_manifest(self) -> List[Dict]:
        """Per-file metadata for ExcelPreprocessor.batch_preprocess()."""
        manifest = []

        for source_type, files in self.discovered_files.items():
            spec = self.registry[source_type]
            rules = {
                "delete_sheets_pattern": spec.processing.delete_sheets_pattern,
                "required_sheets": spec.processing.required_sheets,
                "file_pattern": spec.file_pattern,
            }

            for file in files:
                stat = file.stat()
                manifest.append({
                    "source_path": file,
                    "source_type": source_type,
                    "target_dir": spec.input_dir,
                    "rules": rules,
                    "size_bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    "preprocessing": {
                        "delete_sheets": spec.processing.delete_sheets_pattern,
                        "required_sheets": spec.processing.required_sheets,
                    },
                })

        return manifest