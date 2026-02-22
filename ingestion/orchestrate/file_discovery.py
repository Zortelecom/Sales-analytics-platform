"""Discover and validate source files from SharePoint/local sync"""
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, TYPE_CHECKING
if TYPE_CHECKING:
    from config import SourceConfig

logger = logging.getLogger(__name__)


class FileDiscovery:
    def __init__(self, config: 'SourceConfig'):
        self.config = config
        self.discovered_files = {
            "sales": [],
            "targets": [],
            "references": []
        }

    def discover_all(self) -> Dict[str, List[Path]]:
        """Scan all source directories for valid Excel files"""
        for source_type in ["sales", "targets", "references"]:
            source_path = getattr(self.config, f"{source_type}_path")
            rules = self.config.processing_rules[source_type]

            if not source_path.exists():
                logger.warning("Source path does not exist: %s", source_path)
                continue

            files = self._scan_directory(source_path, rules)
            self.discovered_files[source_type] = files

            logger.info("Discovered %d %s file(s)", len(files), source_type)
            for f in files:
                logger.info("  - %s", f.name)

        return self.discovered_files

    def _scan_directory(self, path: Path, rules: Dict) -> List[Path]:
        """Scan single directory with validation"""
        pattern = rules.get("file_pattern", "*.xlsx")
        files = list(path.glob(pattern))

        valid_files = []
        for file in files:
            if self._validate_file(file):
                valid_files.append(file)
            else:
                logger.warning("Skipping invalid file: %s", file)

        # Sort by modification time (newest first)
        valid_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        return valid_files

    def _validate_file(self, file: Path) -> bool:
        """Validate single file"""
        # Check extension
        if file.suffix.lower() not in [".xlsx", ".xlsm"]:
            return False

        if file.suffix.lower() == '.xls':
            logger.error("Cannot process .xls file: %s (use .xlsx)", file.name)
            return False

        # Check size (basic corruption check)
        try:
            size_mb = file.stat().st_size / (1024 * 1024)
            if size_mb > 50:  # Max 50MB
                logger.warning(
                    "File too large: %s (%.1fMB)", file.name, size_mb)
                return False
        except OSError:
            return False

        return True

    def check_for_new_files(self, since: Optional[datetime] = None) -> Dict[str, List[Path]]:
        """Filter for files modified since last run"""
        if since is None:
            # Default to files modified in last 24 hours
            since = datetime.now() - timedelta(hours=24)

        new_files = {}
        for source_type, files in self.discovered_files.items():
            new_files[source_type] = [
                f for f in files
                if datetime.fromtimestamp(f.stat().st_mtime) > since
            ]

        return new_files

    def get_processing_manifest(self) -> List[Dict]:
        """Generate manifest of files to process with metadata"""
        manifest = []

        for source_type, files in self.discovered_files.items():
            rules = self.config.processing_rules[source_type]

            for file in files:
                manifest.append({
                    "source_path": file,
                    "source_type": source_type,
                    "target_dir": Path(f"data/input/{source_type}"),
                    "rules": rules,
                    "size_bytes": file.stat().st_size,
                    "modified": datetime.fromtimestamp(file.stat().st_mtime),
                    "preprocessing": {
                        "delete_sheets": rules.get("delete_sheets_pattern"),
                        "required_sheets": rules.get("required_sheets", [])
                    }
                })

        return manifest
