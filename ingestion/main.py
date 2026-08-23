#!/usr/bin/env python3
"""
Ingestion layer entry point. Excel -> DuckLake landing.

LAYER BOUNDARY
──────────────
This module is the whole ingestion layer and nothing else. It imports no
Dagster, no SQLMesh, no serving code. Its output contract is:

    landing.<table>        typed, append-only rows with _-prefixed provenance
    landing.file_registry  one row per (file, ingest attempt)

Everything downstream consumes that contract and nothing else, so the layer
can be developed, run and verified on its own:

    python -m ingestion.main --check      # validate config, never touch data
    python -m ingestion.main --dry-run    # everything except the landing write
    python -m ingestion.main              # full run
    python -m ingestion.main --verify     # what is in landing right now
    pytest tests/ingestion tests/shared   # no Dagster, no lake needed

"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from ingestion.config.landing import (
    CATALOG_ALIAS,
    DUCKLAKE_CATALOG_PATH,
    LANDING_EVOLVE_SCHEMA,
    LANDING_SCHEMA,
    LANDING_SKIP_DUPLICATE_FILES,
    PARQUET_PATH,
)
from ingestion.config.settings import load_sources_config
from ingestion.extract.kp_sd_extractor import KPDestockeExtractor, KPNonDestockeExtractor
from ingestion.extract.reference_extractor import ReferenceExtractor
from ingestion.extract.sales_extractor import SalesExtractor
from ingestion.extract.target_extractor import TargetExtractor
from ingestion.load.landing_writer import LandingWriter
from ingestion.orchestrate import ArchiveManager, ExcelPreprocessor, FileDiscovery
from shared import lake
from shared.paths import ARCHIVE_DIR, DEAD_LETTER_DIR, LOGS_DIR
from shared.sources import load_sources

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# (landing_table, source_type, extractor_class, result_key)
# result_key is set only for extractors returning a dict of frames.
EXTRACTION_PLAN: List[Tuple[str, str, type, str | None]] = [
    ("sales_data", "sales", SalesExtractor, None),
    ("targets_data", "targets", TargetExtractor, None),
    ("kp_sd_destocke_data", "kp_sd", KPDestockeExtractor, None),
    ("kp_sd_non_destocke_data", "kp_sd", KPNonDestockeExtractor, None),
    ("salesteam_data", "references", ReferenceExtractor, "ref_salesteam"),
    ("products_data", "references", ReferenceExtractor, "ref_products"),
    ("clientsd_data", "references", ReferenceExtractor, "ref_clients_sd"),
    ("kp_sku_mapping_data", "references", ReferenceExtractor, "ref_kp_sku_mapping"),
]


def _open_writer(batch_id: str) -> LandingWriter:
    return LandingWriter(
        DUCKLAKE_CATALOG_PATH,
        PARQUET_PATH,
        batch_id=batch_id,
        schema=LANDING_SCHEMA,
        catalog_alias=CATALOG_ALIAS,
        evolve_schema=LANDING_EVOLVE_SCHEMA,
    )


# ── check ───────────────────────────────────────────────────────────────────

def check() -> bool:
    """
    Pre-flight. Validates everything that can be validated without reading a
    workbook or writing a row. Run this first after applying the migration.
    """
    ok = True

    print("Source registry")
    try:
        registry = load_sources()
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  sources.yaml did not parse: {exc}")
        return False
    for source_type, spec in registry.sources.items():
        src_ok = spec.source_dir.exists()
        in_ok = spec.input_dir.exists()
        print(f"  {source_type:11s} source={'ok ' if src_ok else 'MISSING'} "
              f"input={'ok ' if in_ok else 'missing (will be created)'} "
              f"pattern={spec.file_pattern}")
        if not src_ok:
            ok = False

    print("\nColumn contracts")
    try:
        from ingestion.contracts.loader import load_contracts
        contracts = load_contracts()
        print(f"  {len(contracts)} contract(s): {sorted(contracts)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  contracts.yaml did not load: {exc}")
        ok = False

    # WHICH BACKEND IS IN PLAY DECIDES WHAT IS WORTH CHECKING.
    #
    # This block used to report DUCKLAKE_CATALOG_PATH unconditionally, which
    # under the PostgreSQL catalog names a file nothing reads -- and then failed
    # the check because the variable was unset, while the attach on the next
    # line succeeded. A pre-flight check that contradicts itself is worse than
    # no check: the next real failure gets read as more of the same noise.
    postgres = lake.is_postgres_catalog()

    import os

    from shared.env import ENV_FILE, parse_env_file

    from_file = parse_env_file(ENV_FILE)

    print("\nLake")
    print(f"  backend     {lake.describe('writer')}")
    if not postgres:
        print(f"  catalog     {DUCKLAKE_CATALOG_PATH}"
              f"  {'(exists)' if DUCKLAKE_CATALOG_PATH.exists() else '(will be created)'}")
    print(f"  data_path   {PARQUET_PATH}")
    print(f"  alias       {CATALOG_ALIAS}   schema {LANDING_SCHEMA}")
    print(f"  .env        {ENV_FILE}"
          f"  {'(%d vars)' % len(from_file) if from_file else '(not found)'}")

    # PARQUET_PATH matters on both backends -- it is the DATA_PATH DuckLake
    # stores in the catalog at creation, and sqlmesh/config.yaml deliberately
    # does NOT pass one, so this value is the only definition of where table
    # data lives. DUCKLAKE_CATALOG_PATH matters only on the file backend.
    checked = [("PARQUET_PATH", PARQUET_PATH)]
    if not postgres:
        checked.insert(0, ("DUCKLAKE_CATALOG_PATH", DUCKLAKE_CATALOG_PATH))

    if postgres:
        # Set-but-unused is the trap here, not unset. It makes every path
        # report in this output describe a catalog no process opens.
        if os.getenv("DUCKLAKE_CATALOG_PATH"):
            print("  WARN  DUCKLAKE_CATALOG_PATH is set but ignored: "
                  "PG_CATALOG_HOST selects the PostgreSQL catalog. Unset it, "
                  "or unset PG_CATALOG_HOST to go back to the file catalog.")
        for role in ("reader", "publisher"):
            if not os.getenv(f"PG_CATALOG_USER_{role.upper()}"):
                print(f"  WARN  PG_CATALOG_USER_{role.upper()} unset — role "
                      f"\"{role}\" will connect as PG_CATALOG_USER "
                      f"({os.getenv('PG_CATALOG_USER')}), so least privilege "
                      f"is not actually in force.")

    for name, resolved in checked:
        raw = os.getenv(name)
        if raw is None:
            print(f"  WARN  {name} unset in both .env and the environment — "
                  f"using default {resolved}.")
            ok = False
        elif "$" in raw:
            print(f"  FAIL  {name}={raw!r} contains shell syntax. A .env file is "
                  f"not a shell: $(pwd) and ${{VAR}} are literal text. Use an "
                  f"absolute path or one relative to the project root.")
            ok = False
        elif not resolved.parent.exists():
            print(f"  FAIL  {name}={raw} resolves to {resolved}, "
                  f"whose parent does not exist")
            ok = False
        else:
            source = "env" if name not in from_file else ".env"
            print(f"  {name:22s} ok ({source})")

    try:
        with _open_writer("check"):
            print("  attach      ok")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  could not attach the lake: {exc}")
        ok = False

    # Discovery is where a wrong file_pattern shows up, and it is silent:
    # _scan_directory falls back to "*.xlsx" rather than raising.
    print("\nDiscovery (dry)")
    try:
        discovered = FileDiscovery(load_sources_config()).discover_all()
        for source_type, files in discovered.items():
            print(f"  {source_type:11s} {len(files):3d} file(s)"
                  + (f"  e.g. {files[0].name}" if files else ""))
        if not any(discovered.values()):
            print("  FAIL  no files discovered in any source directory")
            ok = False
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  discovery raised: {exc}")
        ok = False

    print("\n" + ("All checks passed." if ok else "Fix the above before running."))
    return ok


# ── verify ──────────────────────────────────────────────────────────────────

def verify() -> bool:
    """Report the ingestion layer's output contract. Reads only."""
    with _open_writer("verify") as writer:
        summary = writer.summary()
        registry = writer.registry_summary()
        observability = writer.observability_summary()

    if summary.empty:
        logger.error("Landing is empty at %s — has the pipeline run?", DUCKLAKE_CATALOG_PATH)
        return False

    print("\nLanding tables")
    print(summary.to_string(index=False))
    print("\nFile registry")
    print(registry.to_string(index=False))

    # Listed because their EXISTENCE is the precondition for `sqlmesh plan`:
    # meta.audit_results and meta.audit_failures are views over them, and
    # DuckDB validates a view's references at CREATE VIEW. Empty is fine;
    # absent stops the transformation layer before it starts.
    print("\nObservability tables (created empty by the landing writer)")
    print(observability.to_string(index=False))

    absent = observability.loc[~observability["exists"], "table"].tolist()
    if absent:
        logger.error(
            "Missing from the landing schema: %s. `sqlmesh plan` will fail "
            "building the meta views over them.", absent,
        )

    missing = {table for table, _, _, _ in EXTRACTION_PLAN} - set(summary["table"])
    if missing:
        logger.warning("Declared but never landed: %s", sorted(missing))
    empty = summary.loc[summary["current_rows"] == 0, "table"].tolist()
    if empty:
        logger.warning("Landed but currently empty: %s", empty)

    return not missing and not empty and not absent


# ── extraction ──────────────────────────────────────────────────────────────

def extract_all(batch_id: str) -> Tuple[Dict[str, pd.DataFrame], List[str]]:
    """
    Run every extractor once. Reference extraction is cached so the workbook is
    opened once rather than four times.

    A failing source is recorded and skipped rather than aborting the run: one
    unreadable workbook should not cost you the other seven tables.

    But the caller must decide whether the surviving sources are enough. A
    shared dependency -- a malformed contracts.yaml, a missing input directory
    -- fails EVERY source at once, and reporting that as a partial success is
    worse than failing: the run exits 0, the workbooks get archived, and
    nothing downstream knows the lake was not updated. Hence the failure list
    is returned rather than only logged.
    """
    registry = load_sources()
    frames: Dict[str, pd.DataFrame] = {}
    reference_cache: Dict[str, pd.DataFrame] | None = None
    failures: List[str] = []

    for table, source_type, extractor_class, result_key in EXTRACTION_PLAN:
        input_dir = registry[source_type].input_dir
        try:
            if result_key is not None:
                if reference_cache is None:
                    reference_cache = extractor_class(batch_id=batch_id).read(input_dir) or {}
                frame = reference_cache.get(result_key)
            else:
                extractor = extractor_class(batch_id=batch_id)
                frame = extractor.read(input_dir)
                details = getattr(extractor, "coercion_details", {})
                if details:
                    logger.warning("%s: mixed-type columns coerced to string", table)
                    for column, by_type in sorted(details.items()):
                        summary = ", ".join(
                            f"{name}={info['rows']} (e.g. {info['sample']!r})"
                            for name, info in sorted(
                                by_type.items(), key=lambda kv: kv[1]["rows"])
                        )
                        logger.warning("    %-18s %s", column, summary)

            if frame is None or frame.empty:
                logger.warning("%-24s no rows", table)
                continue

            frames[table] = frame
            logger.info("%-24s %6d rows from %d file(s)",
                        table, len(frame),
                        frame["source_file"].nunique() if "source_file" in frame else 1)

        except Exception as exc:  # noqa: BLE001 — one bad source must not sink the rest
            failures.append(f"{table}: {exc}")
            logger.error("%-24s FAILED: %s", table, exc, exc_info=True)

    if failures:
        logger.warning("%d source(s) failed extraction:", len(failures))
        for failure in failures:
            logger.warning("    %s", failure)
    return frames, failures


# ── pipeline ────────────────────────────────────────────────────────────────

def run_ingestion_pipeline(
    dry_run: bool = False,
    skip_archive: bool = False,
    since: datetime = None,
) -> bool:
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger.info("Starting ingestion [batch %s]", batch_id)

    # ── Phase 1: discovery ──────────────────────────────────────────────────
    logger.info("Phase 1: file discovery")
    config = load_sources_config()
    discovery = FileDiscovery(config)

    # REQUIRED. check_for_new_files() and get_processing_manifest() both read
    # self.discovered_files, which only discover_all() populates. Without this
    # the run reports "no new files" and exits 0 having done nothing.
    discovered = discovery.discover_all()
    logger.info("Discovered %d file(s) across %d source(s)",
                sum(len(v) for v in discovered.values()), len(discovered))

    new_files = discovery.check_for_new_files(since=since)
    total_new = sum(len(v) for v in new_files.values())
    if total_new == 0:
        logger.info("No new files to process")
        return True
    logger.info("Found %d new file(s)", total_new)

    new_file_paths = {f.resolve() for files in new_files.values() for f in files}
    manifest = [
        m for m in discovery.get_processing_manifest()
        if m["source_path"].resolve() in new_file_paths
    ]

    # ── Phase 2: preprocessing ──────────────────────────────────────────────
    logger.info("Phase 2: preprocessing")
    # NOT ExcelPreprocessor(dry_run=dry_run). Preprocessing writes only to
    # data/input/, which is derived scratch space, and extraction reads from
    # there -- so skipping it made --dry-run extract nothing whenever
    # data/input was empty, while reporting success. What a dry run must avoid
    # is the landing write and the archive, both of which are skipped below.
    preprocessor = ExcelPreprocessor(dry_run=False)
    results = preprocessor.batch_preprocess(manifest)

    if results["failed"]:
        logger.warning("Preprocessing failed for %d file(s)", len(results["failed"]))
        # batch_preprocess appends the raw manifest item, which has no "error"
        # key -- the messages accumulate on preprocessor.stats instead. Passing
        # them through is why the old manifest always said "Unknown error".
        _record_failures(batch_id, results["failed"], preprocessor.stats.get("errors", []))
        if not results["successful"]:
            logger.error("No files survived preprocessing. Aborting.")
            return False
        logger.warning("Continuing with %d file(s)", len(results["successful"]))

    logger.info("Preprocessed %d, skipped %d (already current)",
                len(results["successful"]), len(results["skipped"]))

    # ── Phase 3: extraction ─────────────────────────────────────────────────
    logger.info("Phase 3: extraction")
    for spec in load_sources().sources.values():
        spec.input_dir.mkdir(parents=True, exist_ok=True)

    frames, failures = extract_all(batch_id)

    # A shared dependency failing takes every source with it. Treat "most
    # sources failed" as a failed run, not a partial one -- otherwise the
    # workbooks get archived below as though they had been ingested.
    if failures and len(failures) > len(frames):
        logger.error(
            "%d source(s) failed and only %d succeeded. This looks like a "
            "shared failure (contracts.yaml, a missing input directory, the "
            "lake) rather than a bad workbook. Nothing will be landed or "
            "archived. Run `python -m ingestion.main --check` to see why.",
            len(failures), len(frames),
        )
        return False

    if not frames:
        logger.error(
            "Files were preprocessed but no rows were extracted. Check that "
            "data/input/<source>/ contains the cleaned workbooks — extraction "
            "reads from there, not from the source folder."
        )
        return False

    if dry_run:
        logger.info("[DRY RUN] would land: %s",
                    {t: len(f) for t, f in frames.items()})
        return not failures

    # ── Phase 4: load to landing ────────────────────────────────────────────
    logger.info("Phase 4: load to landing")
    registry = load_sources()
    landed: Dict[str, Dict[str, int]] = {}

    with _open_writer(batch_id) as writer:
        for table, source_type, _, _ in EXTRACTION_PLAN:
            frame = frames.get(table)
            if frame is None:
                continue
            result = writer.append_directory_frame(
                table,
                frame,
                source_type=source_type,
                input_dir=registry[source_type].input_dir,
                skip_duplicates=LANDING_SKIP_DUPLICATE_FILES,
            )
            if result:
                landed[table] = result
            else:
                # Either every file was unchanged, or -- the bug this now
                # guards against -- the table was silently skipped.
                logger.info("%-24s nothing new to land", table)
        print("\nLanding tables")
        print(writer.summary().to_string(index=False))

    total = sum(sum(files.values()) for files in landed.values())
    logger.info("Landed %d row(s) across %d table(s)", total, len(landed))

    # Archiving marks a workbook as dealt with. Skip it when any source failed,
    # so a re-run after the fix still sees the files as new.
    if failures:
        logger.warning(
            "%d source(s) failed; skipping the archive step so the affected "
            "workbooks are re-processed on the next run.", len(failures),
        )
        skip_archive = True

    # ── Phase 5: archive ────────────────────────────────────────────────────
    if not skip_archive:
        logger.info("Phase 5: archiving")
        archive_path = ArchiveManager(
            batch_id=batch_id, archive_base=ARCHIVE_DIR
        ).archive_processed_files(manifest=results["successful"], move=False)
        logger.info("Archived to %s", archive_path)

    logger.info("Ingestion complete [batch %s]", batch_id)
    return True


def _record_failures(batch_id: str, failed: List[dict], errors: List[str]) -> None:
    """Write a failure manifest and quarantine the files."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    DEAD_LETTER_DIR.mkdir(parents=True, exist_ok=True)

    manifest_path = LOGS_DIR / f"failures_{batch_id}.txt"
    with open(manifest_path, "w", encoding="utf-8") as handle:
        handle.write(f"Preprocessing failures - batch {batch_id}\n")
        handle.write(f"Failed files: {len(failed)}\n{'=' * 80}\n\n")
        for item in failed:
            name = Path(item["source_path"]).name
            matched = [e for e in errors if name in e] or ["(no message captured)"]
            handle.write(f"File: {item['source_path']}\n")
            for message in matched:
                handle.write(f"Error: {message}\n")
            handle.write(f"{'-' * 80}\n")
    logger.info("Failure manifest: %s", manifest_path)

    for item in failed:
        path = Path(item["source_path"])
        if not path.exists():
            logger.warning("  not found: %s", path)
            continue
        try:
            shutil.move(str(path), str(DEAD_LETTER_DIR / path.name))
            logger.warning("  moved to dead_letter: %s", path.name)
        except OSError as exc:
            logger.warning("  could not move %s: %s", path.name, exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sales Analytics ingestion layer")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preprocess and extract, but write nothing to the "
                             "lake and skip archiving")
    parser.add_argument("--skip-archive", action="store_true")
    parser.add_argument("--since", type=str, metavar="YYYY-MM-DD",
                        help="Only files modified since this date (UTC)")
    parser.add_argument("--all", action="store_true",
                        help="Every discovered file, ignoring mtime. Use for the "
                             "first load: the default window is only 24 hours.")
    parser.add_argument("--verify", action="store_true",
                        help="Report what is currently in landing and exit")
    parser.add_argument("--check", action="store_true",
                        help="Validate configuration without touching data")
    args = parser.parse_args()

    if args.check:
        return 0 if check() else 1
    if args.verify:
        return 0 if verify() else 1

    # FileDiscovery compares against tz-aware UTC mtimes, so a naive datetime
    # here raises TypeError ("can't compare offset-naive and offset-aware").
    if args.all:
        # Everything. check_for_new_files() defaults to the LAST 24 HOURS,
        # which is not what you want for an initial load into landing.
        since = datetime(1970, 1, 1, tzinfo=timezone.utc)
    elif args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        since = None   # FileDiscovery's 24h default

    ok = run_ingestion_pipeline(
        dry_run=args.dry_run, skip_archive=args.skip_archive, since=since
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())