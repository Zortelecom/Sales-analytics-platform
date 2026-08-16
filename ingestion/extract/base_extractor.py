"""
Base class for extracting data from Excel Table objects.

(2026-08) TYPE PRESERVATION — the significant change in this version
────────────────────────────────────────────────────────────────────
The previous version ended extraction with:

    for col in df.columns:
        if col != "ingestion_ts":
            df[col] = df[col].astype(str)

That was correct when the destination was a CSV seed, but it caused two
problems that outlived the reason for it:

  1. astype(str) renders a missing cell as the literal string "nan", not
     NULL. Every downstream `WHERE sale_date IS NOT NULL AND sku IS NOT NULL`
     guard silently passes those rows through, because "nan" is a perfectly
     good non-null VARCHAR. Check whether has_null_key in the subclasses is
     computed before or after this loop — if after, it has never fired.

  2. Dates became strings like "2025-12-01 00:00:00" and quantities became
     "10", pushing every cast into the staging layer and losing openpyxl's
     already-correct typing on the way.

Blanket str is nevertheless the right answer for ONE case: a column where
supervisors have mixed types — a qty column holding both the number 12 and
the text "12". Writing that to a typed column fails or silently coerces.

So the coercion is now per column, not blanket: a column with a single
non-null Python type keeps that type; a genuinely mixed column falls back to
string. Mixed columns are recorded in `self.coerced_columns` so the fallback
is visible rather than silent — it is a hand-entry signal worth counting.

Header normalisation is also new: headers are lowercased, stripped, and
internal whitespace collapsed. The required_columns check already compared
against lowercased names, so this closes the gap where a header typed
"SD_ID " passed validation but produced an unreachable column.

Earlier fixes retained
──────────────────────
1. wb.close() in a finally block so the handle is always released.
2. validate_sheet_name() as a real method with a pass-all default.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
from decimal import Decimal
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import openpyxl
import pandas as pd

logger = logging.getLogger(__name__)

_WHITESPACE = re.compile(r"\s+")

# Placeholder text that means "no value". The subclasses already null these out
# via df.replace([...], np.nan) after extraction; they are checked HERE instead
# because per-column type detection runs first. A clean integer qty column with
# one "-" placeholder would otherwise look like {int, str} and be coerced to
# string, which is the opposite of what type preservation is for.
#
# Note this treats a literal "-" as missing. That is the behaviour the
# subclasses already had, kept deliberately rather than inherited by accident.
MISSING_SENTINELS = frozenset({"none", "nat", "nan", "n/a", "#n/a", "-", "--", "null"})

# A column holding several of these is not "mixed" in any meaningful sense --
# it is numeric, and stringifying it would push a CAST failure into staging.
# 2 and 2.5 in one unit_weight column is a formatting difference, not a type
# conflict. Same for a column where some cells are dates and others datetimes.
NUMERIC_TYPES = (int, float, Decimal)

# Thousands separators seen in French-locale Excel: ordinary space, non-breaking
# space (U+00A0) and narrow no-break space (U+202F).
_SPACES = str.maketrans({" ": "", "\u00a0": "", "\u202f": "", "\t": ""})
_THOUSANDS_GROUPED = re.compile(r"^[+-]?\d{1,3}(,\d{3})+$")
_PLAIN_NUMBER = re.compile(r"^[+-]?(\d+(\.\d+)?|\.\d+)$")
TEMPORAL_TYPES = (dt.date, dt.datetime, pd.Timestamp)

# Columns the extractor adds itself. LandingWriter renames these to _-prefixed
# provenance columns; nothing downstream should treat them as business data.
PROVENANCE_SOURCE_COLUMNS = [
    "source_file",
    "sheet_name",
    "table_name",
    "ingestion_ts",
    "ingestion_batch_id",
]


def normalize_header(value: Any, position: int) -> str:
    """
    Lowercase, strip, collapse internal whitespace.

    `if value is None` rather than `if value` on purpose: a header cell
    containing 0 is a real header, and the old truthiness test replaced it
    with a col_N placeholder.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return f"col_{position}"
    text = _WHITESPACE.sub(" ", str(value).strip()).lower()
    return text or f"col_{position}"


class BaseExcelExtractor:
    """Extracts named Excel Tables with consistent metadata and error handling."""

    # Subclasses may set True to restore the old blanket-string behaviour.
    COERCE_ALL_TO_STRING: bool = False

    def __init__(self, batch_id: str) -> None:
        self.batch_id = batch_id
        self.ingestion_ts = datetime.now(timezone.utc)
        # column name -> set of Python type names seen, for columns that had to
        # be coerced. Read by the ingestion asset and surfaced as metadata.
        self.coerced_columns: Dict[str, Set[str]] = defaultdict(set)
        # Same columns, with row counts and a sample value per type. "Mixed"
        # alone does not distinguish three stray zeros from half the file, and
        # the two call for completely different responses.
        self.coercion_details: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)

    # ── Sheet-level hook ────────────────────────────────────────────────────

    def validate_sheet_name(self, sheet_name: str, file_path: Path) -> bool:
        """True if this sheet should be processed. Default accepts everything."""
        return True

    # ── Workbook access ─────────────────────────────────────────────────────

    def _get_workbook(self, file_path: Path) -> openpyxl.Workbook:
        """
        read_only=False is required: read-only mode does not expose the
        .tables attribute, and named Tables are how every source is structured.
        """
        try:
            return openpyxl.load_workbook(
                file_path, data_only=True, read_only=False, keep_links=False
            )
        except Exception as exc:
            logger.error("Could not open workbook %s: %s", file_path.name, exc)
            raise

    # ── Type handling ───────────────────────────────────────────────────────

    def _normalize_types(self, df: pd.DataFrame, context: str) -> pd.DataFrame:
        """
        Preserve per-column types; fall back to string only where mixed.

        Missing values become None (SQL NULL), never the string "nan".
        """
        if self.COERCE_ALL_TO_STRING:
            for column in df.columns:
                df[column] = _object_series(
                    [None if _is_missing(v) else _stringify(v) for v in df[column]],
                    df.index,
                )
            return df

        for column in df.columns:
            values = df[column].tolist()
            present = [v for v in values if not _is_missing(v)]

            if not present:
                # An entirely empty column tells us nothing about its type, and
                # DuckDB guesses INTEGER for an all-None object column. When the
                # next file has a real value the insert dies with
                # "Could not convert string 'X' to INT32". Text accepts anything.
                df[column] = pd.array([None] * len(values), dtype="string")
                continue

            observed = {type(v) for v in present}
            # bool subclasses int; do not stringify a TRUE/FALSE column just
            # because one cell came through as 0.
            if observed <= {bool, int}:
                observed = {int}

            if len(observed) > 1 and str in observed:
                # A column is not "mixed" just because some cells are text. If
                # EVERY value parses as a number, it is a numeric column whose
                # cells were formatted as text -- which is what unit_weight is:
                # entirely text in some workbooks, float in others, all of it
                # parseable. Stringifying it would push a CAST into staging and
                # make arithmetic on it silently wrong.
                parsed = _try_all_numeric(values)
                if parsed is not None:
                    text_rows = sum(1 for v in present if isinstance(v, str))
                    logger.info(
                        "%s: column %r has %d value(s) stored as text but all "
                        "parse as numbers — reading the column as numeric",
                        context, column, text_rows,
                    )
                    df[column] = _numeric_series(parsed, df.index)
                    continue

            if len(observed) > 1 and all(issubclass(t, NUMERIC_TYPES) for t in observed):
                # Widen rather than stringify. bool was already folded into int
                # above, so this is a genuine int/float/Decimal mix.
                logger.info(
                    "%s: column %r mixes %s — widening to numeric",
                    context, column,
                    sorted(t.__name__ for t in observed),
                )
                cleaned = [None if _is_missing(v) else float(v) for v in values]
                df[column] = _numeric_series(cleaned, df.index)
                continue

            if len(observed) > 1 and all(issubclass(t, TEMPORAL_TYPES) for t in observed):
                # date + datetime in one column: same instant, different Excel
                # cell formatting. pandas resolves it to datetime64.
                logger.info(
                    "%s: column %r mixes %s — widening to datetime",
                    context, column,
                    sorted(t.__name__ for t in observed),
                )
                df[column] = pd.to_datetime(
                    pd.Series(
                        [None if _is_missing(v) else v for v in values], index=df.index
                    ),
                    errors="coerce",
                )
                continue

            if len(observed) > 1:
                type_names = sorted(t.__name__ for t in observed)
                self.coerced_columns[column].update(type_names)

                counts = Counter(type(v).__name__ for v in present)
                samples: Dict[str, Any] = {}
                for value in present:
                    samples.setdefault(type(value).__name__, value)

                detail = {
                    name: {"rows": counts[name], "sample": samples.get(name)}
                    for name in sorted(counts, key=counts.get, reverse=True)
                }
                self.coercion_details[column].update(detail)

                # Minority types first: those are the odd cells to go and look
                # at in the workbook. A single int 0 among 500 strings is an
                # empty VLOOKUP; a string among floats is usually a decimal
                # typed with a comma.
                breakdown = ", ".join(
                    f"{name}={counts[name]} (e.g. {samples[name]!r})"
                    for name in sorted(counts, key=counts.get)
                )
                logger.warning(
                    "%s: column %r mixed — %s — coercing to string",
                    context, column, breakdown,
                )
                df[column] = _object_series(
                    [None if _is_missing(v) else _stringify(v) for v in values],
                    df.index,
                )
                continue

            only = next(iter(observed))
            cleaned = [
                None if _is_missing(v) else (v.strip() if only is str else v)
                for v in values
            ]

            if only is float:
                df[column] = _numeric_series(cleaned, df.index)
            else:
                df[column] = _object_series(cleaned, df.index)

        return df

    # ── Single-file extraction ──────────────────────────────────────────────

    def extract_single_file(
        self,
        file_path: Path,
        table_prefix: str,
        exclude_sheets: List[str],
        required_columns: Optional[Set[str]] = None,
    ) -> pd.DataFrame:
        """Collect every named Table in the workbook whose name starts with table_prefix."""
        wb = self._get_workbook(file_path)
        file_frames: List[pd.DataFrame] = []

        try:
            for sheet_name in wb.sheetnames:
                if (
                    sheet_name.lower() in exclude_sheets
                    or sheet_name.lower().startswith("synthese")
                ):
                    logger.debug("Skipping excluded sheet: %s", sheet_name)
                    continue

                if not self.validate_sheet_name(sheet_name, file_path):
                    continue

                ws = wb[sheet_name]
                if not getattr(ws, "tables", None):
                    logger.debug("Sheet %s has no tables, skipping", sheet_name)
                    continue

                try:
                    table_names = (
                        list(ws.tables.keys())
                        if hasattr(ws.tables, "keys")
                        else list(ws.tables)
                    )
                except AttributeError:
                    table_names = list(ws.tables)

                for table_name in table_names:
                    if not table_name.startswith(table_prefix):
                        continue

                    try:
                        table = ws.tables[table_name]
                        logger.info("Found table: %s in sheet %s", table_name, sheet_name)

                        df = self._table_to_df(ws, table.ref)
                        if df is None or df.empty:
                            continue

                        if required_columns:
                            present = set(df.columns)  # already normalised
                            missing = {c.lower().strip() for c in required_columns} - present
                            if missing:
                                logger.error(
                                    "SCHEMA ERROR in File: '%s' | Sheet: '%s' | Table: '%s'",
                                    file_path.name, sheet_name, table_name,
                                )
                                logger.error("   Present columns: %s", sorted(present))
                                logger.error("   Missing columns: %s", sorted(missing))
                                continue  # skip this table, don't pollute the dataset

                        # Types are normalised after the concat below, not
                        # here: two tables in one workbook can type the same
                        # column differently, and the merged frame is what has
                        # to be internally consistent.
                        df["source_file"] = file_path.name
                        df["sheet_name"] = sheet_name
                        df["table_name"] = table_name
                        df["ingestion_ts"] = self.ingestion_ts
                        df["ingestion_batch_id"] = self.batch_id

                        file_frames.append(df)
                        logger.info("Extracted %d rows from table %s", len(df), table_name)

                    except Exception as exc:
                        logger.error(
                            "Error processing table %s: %s", table_name, exc, exc_info=True
                        )
                        continue
        finally:
            # Critical on Windows: an open workbook blocks the next write.
            wb.close()

        if not file_frames:
            logger.warning("No tables extracted from %s", file_path.name)
            return pd.DataFrame()

        merged = pd.concat(file_frames, ignore_index=True, sort=False)
        return self._normalize_types(merged, file_path.name)

    # ── Table → DataFrame ───────────────────────────────────────────────────

    def _table_to_df(self, ws, table_ref: str) -> Optional[pd.DataFrame]:
        try:
            data = list(ws[table_ref])
            if not data:
                return None

            headers = [
                normalize_header(cell.value, i) for i, cell in enumerate(data[0])
            ]
            headers = _dedupe(headers)

            values = [[cell.value for cell in row] for row in data[1:]]
            df = pd.DataFrame(values, columns=headers)
            df.dropna(how="all", inplace=True)

            if df.empty:
                logger.warning("Table at %s has no data rows", table_ref)
                return None
            return df

        except Exception as exc:
            logger.error("Error converting table at %s: %s", table_ref, exc, exc_info=True)
            return None

    # ── Directory-level extraction ──────────────────────────────────────────

    def extract_from_directory(
        self,
        directory: Path,
        table_prefix: str,
        exclude_sheets: List[str] = None,
        file_pattern: str = "*.xlsx",
        required_columns: Optional[Set[str]] = None,
    ) -> pd.DataFrame:
        """One corrupt file is logged and skipped rather than aborting the batch."""
        frames: List[pd.DataFrame] = []
        exclude_sheets = [s.lower() for s in (exclude_sheets or [])]

        if not directory.exists():
            logger.warning("Directory %s does not exist.", directory)
            return pd.DataFrame()

        matching_files = sorted(directory.glob(file_pattern))
        if not matching_files:
            logger.warning("No files matching pattern '%s' in %s", file_pattern, directory)
            return pd.DataFrame()

        for file_path in matching_files:
            if file_path.name.startswith("~$"):
                continue  # Excel lock file

            logger.info("Processing %s file: %s", table_prefix, file_path.name)
            try:
                df_file = self.extract_single_file(
                    file_path, table_prefix, exclude_sheets, required_columns
                )
                if not df_file.empty:
                    frames.append(df_file)
            except Exception as exc:
                logger.error("Failed to process %s: %s", file_path.name, exc, exc_info=True)
                continue

        if not frames:
            logger.warning("No data extracted from any files in %s", directory)
            return pd.DataFrame()

        # Different files may legitimately have different columns after schema
        # drift; concat unions them and fills the gaps with NULL rather than
        # dropping the odd one out.
        merged = pd.concat(frames, ignore_index=True, sort=False)

        # Second normalisation pass, across files. Each file was made internally
        # consistent above, but two files can still disagree: sd_name is blank in
        # one KP workbook (SD encoded in the sheet name) and populated in
        # another. Without this the landing table is created from whichever file
        # sorts first, and the second one fails to insert.
        return self._normalize_types(merged, f"{directory.name} (all files)")


def _parse_numeric_text(text: str) -> Optional[float]:
    """
    Parse a number that Excel stored as text, or None if it is not one.

    Refuses anything with a leading zero on the integer part ("007", "0123"):
    those are identifiers, and turning them into numbers loses the padding that
    makes them identifiers.

    Refuses an ambiguous single comma with exactly three following digits
    ("1,500" — 1.5 or 1500?) unless the whole value matches a thousands-grouped
    pattern. Better to fall back to string and report it than to guess wrong on
    a monetary amount.
    """
    cleaned = text.strip().translate(_SPACES)
    if not cleaned:
        return None

    if _THOUSANDS_GROUPED.match(cleaned):
        cleaned = cleaned.replace(",", "")
    elif cleaned.count(",") == 1 and "." not in cleaned:
        whole, _, frac = cleaned.partition(",")
        if len(frac) == 3:
            return None  # ambiguous grouping vs decimal
        cleaned = f"{whole}.{frac}"

    if not _PLAIN_NUMBER.match(cleaned):
        return None

    digits = cleaned.lstrip("+-").split(".")[0]
    if len(digits) > 1 and digits.startswith("0"):
        return None  # zero-padded identifier, not a measurement

    try:
        return float(cleaned)
    except ValueError:
        return None


def _try_all_numeric(values: List[Any]) -> Optional[List[Optional[float]]]:
    """Every non-null value as a float, or None if any one of them is not numeric."""
    out: List[Optional[float]] = []
    for value in values:
        if _is_missing(value):
            out.append(None)
        elif isinstance(value, bool):
            return None  # a bool among text is not a numeric column
        elif isinstance(value, NUMERIC_TYPES):
            out.append(float(value))
        elif isinstance(value, str):
            parsed = _parse_numeric_text(value)
            if parsed is None:
                return None
            out.append(parsed)
        else:
            return None
    return out


def _numeric_series(values: List[Any], index):
    """
    Float values, kept as integers when every one of them is whole.

    A quantity column picks up float dtype the moment one cell is blank. XAF
    amounts and quantities have no fractional part, so keeping them float
    pushes an avoidable cast into staging and risks 1e6-style rendering in
    exports.
    """
    present = [v for v in values if v is not None]
    if present and all(float(v).is_integer() for v in present):
        return pd.array(
            [None if v is None else int(v) for v in values], dtype="Int64"
        )
    return pd.array([None if v is None else float(v) for v in values], dtype="Float64")


def _object_series(values: List[Any], index) -> pd.Series:
    """
    Build an object-dtype Series that keeps None as None.

    pd.Series(...) and Series.map() both re-infer and turn None back into
    NaN, which lands in DuckDB as the string "nan" or a float NaN rather
    than NULL. Constructing with dtype=object explicitly is the only
    reliable way to preserve it.
    """
    series = pd.Series(index=index, dtype=object)
    series.iloc[:] = values
    return series


def stable_row_id(
    df: pd.DataFrame,
    key_columns: List[str],
    scope_columns: List[str] = ("source_file",),
    prefix: str = "",
) -> List[str]:
    """
    Deterministic surrogate key for a source row.

    Replaces uuid.uuid4(). A uuid is regenerated on every run, which was
    invisible while seeds were overwritten wholesale but breaks two things
    under append-only landing:

      1. Two ingests of the same workbook cannot be diffed, because every
         row's identity changed. Quantifying a correction -- "these 340
         lines moved channel" -- becomes impossible.
      2. fact_kp_sd declares grain (kp_sd_line_id). Re-running any
         incremental interval reissues every ID, so nothing downstream can
         hold a stable reference to a fact row across a restatement.

    The ID hashes the BUSINESS KEY, not the whole row, so correcting a
    quantity preserves the line's identity and the change is visible as a
    change rather than as a delete plus an insert.

    Genuine duplicates -- two identical lines that both legitimately exist --
    are disambiguated by an occurrence counter, so uniqueness still holds.
    Inserting a row mid-file does not disturb its neighbours' IDs.

    Stability depends on the key columns' types being stable between runs.
    That holds now that the extractor preserves types; it would not have held
    under the old blanket astype(str).
    """
    columns = [c for c in (*scope_columns, *key_columns) if c in df.columns]
    if not columns:
        raise ValueError(f"stable_row_id: none of {key_columns} present in frame")

    keys = [
        "\x1f".join("" if _is_missing(v) else _stringify(v) for v in row)
        for row in df[columns].itertuples(index=False, name=None)
    ]
    occurrence = pd.Series(keys, index=df.index).groupby(keys).cumcount()

    return [
        hashlib.sha1(f"{prefix}\x1e{key}\x1e{n}".encode("utf-8")).hexdigest()[:32]
        for key, n in zip(keys, occurrence)
    ]


def _stringify(value: Any) -> str:
    """
    Render a value as text without pandas' float artefacts.

    A column of [10, None] becomes float64 in pandas, so str() yields "10.0".
    The old blanket astype(str) had this bug: every quantity in a column
    containing a single blank cell was written to the seed as "10.0", and
    CAST('10.0' AS INTEGER) is not the same conversation as CAST('10' AS INTEGER).
    """
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    if value is pd.NaT or value is pd.NA:
        return True
    if isinstance(value, str):
        stripped = value.strip()
        return not stripped or stripped.lower() in MISSING_SENTINELS
    return False


def _dedupe(headers: List[str]) -> List[str]:
    """Two columns normalising to the same name would silently overwrite."""
    seen: Dict[str, int] = {}
    out: List[str] = []
    for header in headers:
        if header in seen:
            seen[header] += 1
            out.append(f"{header}_{seen[header]}")
            logger.warning("Duplicate header %r renamed to %r", header, out[-1])
        else:
            seen[header] = 0
            out.append(header)
    return out