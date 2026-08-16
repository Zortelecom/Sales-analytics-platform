"""
Ingestion assets: Excel -> DuckLake landing.

REPLACES the seed-writing assets. There is no CSV in the path and no
SeedWriter. `sqlmesh/seeds/` and the raw.* SEED models are gone.

SHAPE
─────
Four extract assets return DataFrames; ONE load asset writes them all.

The split is not cosmetic. The DuckLake catalog is a DuckDB file and takes a
single writer, but Dagster materialises assets concurrently by default -- four
writing assets would contend for the catalog lock and fail intermittently.
Extraction is the parallelisable part and stays parallel; the write is
serialised through one connection that closes before SQLMesh opens the lake.

If the catalog moves to PostgreSQL, this can be collapsed back into four
independent writing assets.

DOWNSTREAM CHANGE REQUIRED
──────────────────────────
`sqlmesh_models` in orchestration/assets/transformation.py currently depends
on `seeds_metadata`. Repoint it at `landing_load`. That is the only edit
outside this file and definitions.py.
"""
# NOTE: deliberately NO `from __future__ import annotations`.
#
# PEP 563 turns every annotation into a string, and Dagster resolves the
# `context` parameter by inspecting the actual class:
#
#   DagsterInvalidDefinitionError: Cannot annotate `context` parameter with
#   type AssetExecutionContext
#
# ...which reads as though the annotation is wrong when the annotation is the
# only correct one. Python 3.10+ handles `str | None` and `dict[str, X]`
# natively, so the import buys nothing here.

from datetime import datetime
from pathlib import Path
from typing import Dict

import pandas as pd
from dagster import (
    AssetExecutionContext,
    AssetIn,
    Failure,
    MetadataValue,
    asset,
)

from ingestion.config.landing import (
    CATALOG_ALIAS,
    DUCKLAKE_CATALOG_PATH,
    LANDING_EVOLVE_SCHEMA,
    LANDING_SCHEMA,
    LANDING_SKIP_DUPLICATE_FILES,
    PARQUET_PATH,
)
from ingestion.extract.kp_sd_extractor import KPDestockeExtractor, KPNonDestockeExtractor
from ingestion.extract.reference_extractor import ReferenceExtractor
from ingestion.extract.sales_extractor import SalesExtractor
from ingestion.extract.target_extractor import TargetExtractor
from ingestion.load.landing_writer import LandingWriter
from shared.sources import load_sources


@asset(group_name="ingestion", description="Unique batch identifier for this run")
def current_batch_id(context: AssetExecutionContext) -> str:
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    context.add_output_metadata({"batch_id": batch_id})
    context.log.info("Generated batch ID: %s", batch_id)
    return batch_id


# "skipped" means the copy in data/input/ was already current -- the file is
# THERE and extractable. Gating on "processed" alone meant a run where every
# workbook was up to date extracted nothing at all, even though the input
# directory was full. Only "error" files are genuinely absent, having been
# moved to dead_letter.
EXTRACTABLE_STATUSES = ("processed", "skipped")


def _has_files(preprocessed_files: pd.DataFrame, source_type: str) -> bool:
    """
    Is there anything in this source's input directory worth extracting?

    Note the extractors read the whole DIRECTORY, not the individual files in
    this frame -- so this is a gate, not a file list. That is deliberate: it
    keeps the Dagster path behaving identically to `python -m ingestion.main`,
    which also extracts from the directory. Landing's sha256 check then decides
    what is actually new.
    """
    if preprocessed_files.empty:
        return False
    matched = preprocessed_files[
        (preprocessed_files["source_type"] == source_type)
        & (preprocessed_files["status"].isin(EXTRACTABLE_STATUSES))
    ]
    return not matched.empty


def _extract(context, preprocessed_files, source_type, extractor):
    """Shared body: skip cleanly when there is nothing to do, report coercions."""
    if not _has_files(preprocessed_files, source_type):
        context.log.warning("No preprocessed %s files", source_type)
        return pd.DataFrame()

    input_dir = load_sources()[source_type].input_dir
    df = extractor.read(input_dir)

    if df is None or df.empty:
        raise ValueError(f"{source_type}: files were present but no rows were extracted")

    # Columns where supervisors mixed types in one column, so the extractor had
    # to fall back to string. A hand-entry signal, not a pipeline failure.
    coerced = {c: sorted(t) for c, t in getattr(extractor, "coerced_columns", {}).items()}
    if coerced:
        context.log.warning("%s: mixed-type columns coerced to string: %s", source_type, coerced)

    context.add_output_metadata({
        "row_count": len(df),
        "columns": len(df.columns),
        "source_files": int(df["source_file"].nunique()) if "source_file" in df else 0,
        "mixed_type_columns": MetadataValue.json(coerced) if coerced else "none",
        "preview": MetadataValue.md(df.head(5).to_markdown()),
    })
    return df


@asset(
    group_name="ingestion",
    description="Extract sell-out rows from ExSD workbooks",
    compute_kind="python",
    ins={"preprocessed_files": AssetIn(), "current_batch_id": AssetIn()},
)
def sales_extract(context, preprocessed_files: pd.DataFrame, current_batch_id: str) -> pd.DataFrame:
    return _extract(context, preprocessed_files, "sales", SalesExtractor(batch_id=current_batch_id))


@asset(
    group_name="ingestion",
    description="Extract target rows",
    compute_kind="python",
    ins={"preprocessed_files": AssetIn(), "current_batch_id": AssetIn()},
)
def targets_extract(context, preprocessed_files: pd.DataFrame, current_batch_id: str) -> pd.DataFrame:
    return _extract(context, preprocessed_files, "targets", TargetExtractor(batch_id=current_batch_id))


@asset(
    group_name="ingestion",
    description="Extract reference tables (salesteam, products, clients SD, SKU mapping)",
    compute_kind="python",
    ins={"preprocessed_files": AssetIn(), "current_batch_id": AssetIn()},
)
def references_extract(
    context, preprocessed_files: pd.DataFrame, current_batch_id: str
) -> Dict[str, pd.DataFrame]:
    if not _has_files(preprocessed_files, "references"):
        context.log.warning("No preprocessed reference files")
        return {}

    extractor = ReferenceExtractor(batch_id=current_batch_id)
    results = extractor.read(load_sources()["references"].input_dir)
    if not results:
        raise ValueError("Reference files were present but no tables were extracted")

    context.add_output_metadata({
        "tables": len(results),
        "rows": {k: len(v) for k, v in results.items()},
    })
    return results


@asset(
    group_name="ingestion",
    description="Extract sell-in rows from KP workbooks (destocké + non-destocké)",
    compute_kind="python",
    ins={"preprocessed_files": AssetIn(), "current_batch_id": AssetIn()},
)
def kp_sd_extract(
    context, preprocessed_files: pd.DataFrame, current_batch_id: str
) -> Dict[str, pd.DataFrame]:
    if not _has_files(preprocessed_files, "kp_sd"):
        context.log.warning("No preprocessed KP-SD files")
        return {}

    input_dir = load_sources()["kp_sd"].input_dir
    out: Dict[str, pd.DataFrame] = {}
    for name, extractor in (
        ("kp_sd_destocke_data", KPDestockeExtractor(current_batch_id)),
        ("kp_sd_non_destocke_data", KPNonDestockeExtractor(current_batch_id)),
    ):
        df = extractor.read(input_dir)
        if df is None or df.empty:
            context.log.warning("No rows extracted for %s", name)
            continue
        out[name] = df

    if not out:
        raise Failure(description="KP-SD files were found but no sell-in rows could be extracted")

    context.add_output_metadata({"rows": {k: len(v) for k, v in out.items()}})
    return out


@asset(
    group_name="ingestion",
    description="Append all extracted frames to the DuckLake landing schema",
    compute_kind="duckdb",
    ins={
        "sales_extract": AssetIn(),
        "targets_extract": AssetIn(),
        "references_extract": AssetIn(),
        "kp_sd_extract": AssetIn(),
        "current_batch_id": AssetIn(),
    },
)
def landing_load(
    context: AssetExecutionContext,
    sales_extract: pd.DataFrame,
    targets_extract: pd.DataFrame,
    references_extract: Dict[str, pd.DataFrame],
    kp_sd_extract: Dict[str, pd.DataFrame],
    current_batch_id: str,
) -> dict:
    """
    Single writer. Every landing table is written through one connection,
    which closes before SQLMesh runs.
    """
    registry = load_sources()

    # (landing_table, source_type, frame)
    plan = [
        ("sales_data", "sales", sales_extract),
        ("targets_data", "targets", targets_extract),
        ("salesteam_data", "references", references_extract.get("ref_salesteam")),
        ("products_data", "references", references_extract.get("ref_products")),
        ("clientsd_data", "references", references_extract.get("ref_clients_sd")),
        ("kp_sku_mapping_data", "references", references_extract.get("ref_kp_sku_mapping")),
        ("kp_sd_destocke_data", "kp_sd", kp_sd_extract.get("kp_sd_destocke_data")),
        ("kp_sd_non_destocke_data", "kp_sd", kp_sd_extract.get("kp_sd_non_destocke_data")),
    ]

    written: Dict[str, Dict[str, int]] = {}
    empty: list = []

    with LandingWriter(
        DUCKLAKE_CATALOG_PATH,
        PARQUET_PATH,
        batch_id=current_batch_id,
        schema=LANDING_SCHEMA,
        catalog_alias=CATALOG_ALIAS,
        evolve_schema=LANDING_EVOLVE_SCHEMA,
    ) as writer:
        for table, source_type, frame in plan:
            if frame is None or frame.empty:
                empty.append(table)
                continue
            result = writer.append_directory_frame(
                table,
                frame,
                source_type=source_type,
                input_dir=registry[source_type].input_dir,
                skip_duplicates=LANDING_SKIP_DUPLICATE_FILES,
            )
            written[table] = result
            context.log.info("landing.%s: %s rows across %s file(s)",
                             table, sum(result.values()), len(result))

        stats = writer.stats()

    total = sum(sum(v.values()) for v in written.values())
    supplied = [t for t, _, frame in plan if frame is not None and not frame.empty]

    # Landing nothing is the NORMAL outcome of a re-run: every workbook is
    # unchanged, so every file is skipped by sha256. The earlier guard treated
    # that as a failure and killed a healthy no-op run.
    #
    # What IS wrong is being handed rows and writing none of them without a
    # duplicate skip to explain it -- that means the write silently did
    # nothing. And being handed nothing at all when preprocessing reported
    # files is a gate that is filtering everything out.
    if not supplied:
        context.log.info(
            "No frames to land: every source was empty or already current. "
            "This is a normal no-op re-run."
        )
        context.add_output_metadata({
            "batch_id": current_batch_id,
            "rows_landed": 0,
            "outcome": "nothing to land",
        })
        return {"batch_id": current_batch_id, "rows": 0, "tables": {}, "stats": stats}

    if total == 0 and not stats.get("skipped_duplicate"):
        raise Failure(
            description=(
                f"Rows were extracted for {supplied} but nothing was written "
                f"and nothing was skipped as a duplicate. The landing write "
                f"silently did nothing -- check the file registry."
            )
        )

    context.add_output_metadata({
        "batch_id": current_batch_id,
        "rows_landed": total,
        "tables_written": len(written),
        "tables_empty": MetadataValue.json(empty),
        "registry_stats": MetadataValue.json(stats),
        "catalog": str(DUCKLAKE_CATALOG_PATH),
        "detail": MetadataValue.md(
            pd.DataFrame(
                [(t, f, n) for t, files in written.items() for f, n in files.items()],
                columns=["landing_table", "source_file", "rows"],
            ).to_markdown(index=False) if written else "Nothing written"
        ),
    })

    return {"batch_id": current_batch_id, "rows": total, "tables": written, "stats": stats}