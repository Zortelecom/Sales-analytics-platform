"""
promo_bouclage_extractor.py

Short-term extractor for the promo *closing* reports ("bouclage") — these
already contain computed gains, filled in by hand from documents the
salespeople (DG) or KP invoices pulled from the ERP (SD). This is the fast
path: no tier-resolution logic needed here, we're transcribing numbers
finance/ops already computed, not recomputing them from raw transactions.

IMPORTANT — table_prefix bug found in the real files
-----------------------------------------------------
Excel Table names are NOT a reliable channel indicator: the "ODIST SD
AVRIL-26" sheet (in the *SD* bouclage workbook) uses a table literally named
"dg_odist_avril_2026" — a copy-paste leftover from a DG template. Filtering
with table_prefix="sd_" would silently drop all of ODIST's SD data with no
error. table_prefix is therefore passed as "" (extract every table found;
extract_single_file's `table.startswith("")` always matches) — channel is
decided by which FILE we're reading, never by the table's internal name.

Similarly, the "KP" column inside the table (e.g. "Henri et Frères", "HF")
is more reliable than parsing the KP identity from the sheet name, since
sheet names mix anonymized placeholders ("KP A") with real names ("HF",
"ODIST") inconsistently. We take the most frequent non-null "KP" value per
(source_file, sheet_name) group rather than trusting every row (the first
row of "HF SD Avril-26" has a blank KP cell).

Grain differs by channel, by design — not a bug to reconcile away:
  - DG (destockage): Vendeur x Client (the vendeur's own retail client) —
    no SD identifier at this grain. Matches what was described: sales
    aren't digitized at salesperson level, gains flow KP -> vendeur ->
    client on paper.
  - SD (direct KP invoices via ERP): Client SD — the actual SD, since this
    side already has a digitized source (invoices).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import pandas as pd

from .base_extractor import BaseExcelExtractor

logger = logging.getLogger(__name__)

_PERIOD_RE = re.compile(
    r"(?P<month>janv\w*|f[ée]v\w*|mars|avr\w*|mai|juin|juil\w*|ao[uû]t|"
    r"sept\w*|oct\w*|nov\w*|d[ée]c\w*)[_\-\s]*(?P<year>\d{2,4})",
    re.IGNORECASE,
)

_MONTH_NUM = {
    "janv": "01", "fev": "02", "fév": "02", "mars": "03", "avr": "04",
    "mai": "05", "juin": "06", "juil": "07", "aou": "08", "aoû": "08",
    "sept": "09", "oct": "10", "nov": "11", "dec": "12", "déc": "12",
}


def _period_from_filename(file_path: Path) -> str | None:
    m = _PERIOD_RE.search(file_path.stem)
    if not m:
        return None
    month_key = m.group("month").lower()[:3].replace("é", "e")
    # normalize to the 3-letter keys used in _MONTH_NUM (accepts both accented/plain)
    for k, v in _MONTH_NUM.items():
        if month_key.replace("é", "e").startswith(k.replace("é", "e")[:3]):
            year = m.group("year")
            year = year if len(year) == 4 else f"20{year}"
            return f"{year}-{v}"
    return None


def _region_from_filename(file_path: Path) -> str | None:
    for region in ("CENTRE", "OUEST", "NORD", "SUD", "EST", "LITTORAL"):
        if region in file_path.stem.upper():
            return region
    return None


def _dominant_kp(group: pd.Series) -> str | None:
    non_null = group.dropna()
    non_null = non_null[non_null.str.lower() != "none"]
    if non_null.empty:
        return None
    return non_null.mode().iat[0]


class PromoBouclageExtractor(BaseExcelExtractor):
    """Extracts already-computed promo gains from the closing workbooks."""

    DG_METADATA_COLS = ["Date", "KP", "sousRegion", "Vendeur", "Client",
                         "Quartier", "Localisation", "Tel"]
    SD_METADATA_COLS = ["Date", "KP", "sousRegion", "Vendeur", "Client SD", "Tel"]

    def validate_sheet_name(self, sheet_name: str, file_path: Path) -> bool:
        # Belt-and-braces: base class already skips sheets with no Excel
        # Table, which covers every Synthèse/RECAPITULATIF sheet observed —
        # this override just makes the intent explicit and closes the
        # accent gap in the base class's own filter
        # (`"synthèse".startswith("synthese")` is False, "è" != "e").
        lowered = sheet_name.lower().strip()
        return not (lowered.startswith("synth") or lowered.startswith("recapitulatif")
                    or lowered.startswith("récapitulatif"))

    def _extract_channel(
        self,
        directory: Path,
        file_pattern: str,
        channel: str,
        metadata_cols: list[str],
    ) -> pd.DataFrame:
        df = self.extract_from_directory(
            directory,
            table_prefix="",                     # see module docstring: table names aren't trustworthy here
            exclude_sheets=[],
            file_pattern=file_pattern,
            required_columns=None,                # checked per-file below instead — schemas differ DG vs SD
        )
        if df.empty:
            logger.warning("No %s bouclage data found in %s", channel, directory)
            return df

        df["channel_type"] = channel
        df["period_label"] = df["source_file"].apply(
            lambda f: _period_from_filename(Path(f))
        )
        df["region"] = df["source_file"].apply(
            lambda f: _region_from_filename(Path(f))
        )

        if "KP" in df.columns:
            dominant = (
                df.groupby(["source_file", "sheet_name"])["KP"]
                .transform(_dominant_kp)
            )
            df["kp_name_resolved"] = dominant
        else:
            df["kp_name_resolved"] = None

        # product / gain columns = everything that isn't metadata or provenance
        provenance_cols = {"source_file", "sheet_name", "table_name",
                            "ingestion_ts", "ingestion_batch_id",
                            "channel_type", "period_label", "region",
                            "kp_name_resolved"}
        product_gain_cols = [c for c in df.columns
                              if c not in metadata_cols and c not in provenance_cols]
        df.attrs["product_gain_cols"] = product_gain_cols
        return df

    def extract_dg(self, directory: Path,
                   file_pattern: str = "BOUCLAGE*DG*.xlsx") -> pd.DataFrame:
        return self._extract_channel(directory, file_pattern, "DG", self.DG_METADATA_COLS)

    def extract_sd(self, directory: Path,
                    file_pattern: str = "BOUCLAGE*SD*.xlsx") -> pd.DataFrame:
        return self._extract_channel(directory, file_pattern, "SD", self.SD_METADATA_COLS)

    # ── Wide -> long reshape ────────────────────────────────────────────────
    # Done in Python, not static SQL: the set of product/gain columns
    # changes every period (new SKUs, new reward products), so a fixed
    # UNPIVOT list would need a code change every month. This stays
    # schema-drift-tolerant for the interim path; revisit once the
    # long-term generic model owns this end to end.

    _GAIN_HEADER_RE = re.compile(
        r"gains?\s*\(([^)]+)\)\s*\\?n?\s*(.*)", re.IGNORECASE
    )

    @classmethod
    def melt_to_long(cls, df: pd.DataFrame, client_col: str) -> pd.DataFrame:
        if df.empty:
            return df
        product_gain_cols = df.attrs.get("product_gain_cols", [])
        id_cols = [c for c in df.columns if c not in product_gain_cols]

        long_df = df.melt(
            id_vars=id_cols, value_vars=product_gain_cols,
            var_name="metric_label", value_name="value",
        )

        def _classify(label: str) -> tuple[str, Optional[str], str]:
            m = cls._GAIN_HEADER_RE.match(label.replace("\n", " "))
            if m:
                return "GAIN", m.group(1).strip().upper(), m.group(2).strip()
            return "QTY", None, label.strip()

        classified = long_df["metric_label"].apply(_classify)
        long_df["metric_type"] = classified.apply(lambda x: x[0])
        long_df["unit"] = classified.apply(lambda x: x[1])
        long_df["product_label"] = classified.apply(lambda x: x[2])

        long_df["client_key"] = long_df[client_col]
        long_df["value"] = pd.to_numeric(
            long_df["value"].replace({"None": None, "nan": None}), errors="coerce"
        )
        return long_df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    uploads = Path("/mnt/user-data/uploads")

    extractor = PromoBouclageExtractor(batch_id="test_batch")

    print("=" * 70, "\nDG extraction\n", "=" * 70)
    dg_df = extractor.extract_dg(uploads)
    print(f"shape={dg_df.shape}")
    print("channels:", dg_df["channel_type"].unique() if not dg_df.empty else None)
    print("kp_name_resolved counts:\n", dg_df["kp_name_resolved"].value_counts(dropna=False) if not dg_df.empty else None)
    print("period_label counts:\n", dg_df["period_label"].value_counts(dropna=False) if not dg_df.empty else None)
    print("product/gain cols:", dg_df.attrs.get("product_gain_cols") if not dg_df.empty else None)

    print()
    print("=" * 70, "\nSD extraction\n", "=" * 70)
    sd_df = extractor.extract_sd(uploads)
    print(f"shape={sd_df.shape}")
    print("kp_name_resolved counts:\n", sd_df["kp_name_resolved"].value_counts(dropna=False) if not sd_df.empty else None)
    print("period_label counts:\n", sd_df["period_label"].value_counts(dropna=False) if not sd_df.empty else None)
    print("sheet_name x table_name (checking the dg_/sd_ prefix mismatch):")
    if not sd_df.empty:
        print(sd_df[["sheet_name", "table_name"]].drop_duplicates())

    print()
    print("=" * 70, "\nLong-format reshape (SD)\n", "=" * 70)
    sd_long = PromoBouclageExtractor.melt_to_long(sd_df, client_col="Client SD")
    print(f"shape={sd_long.shape}")
    print(sd_long["metric_type"].value_counts())
    print(sd_long[sd_long["metric_type"] == "GAIN"][["product_label", "unit", "value"]].dropna().head(8))

    print()
    print("=" * 70, "\nLong-format reshape (DG)\n", "=" * 70)
    dg_long = PromoBouclageExtractor.melt_to_long(dg_df, client_col="Client")
    print(f"shape={dg_long.shape}")
    print(dg_long["metric_type"].value_counts())
    print(dg_long[dg_long["metric_type"] == "GAIN"][["product_label", "unit", "value"]].dropna().head(8))

    # sanity check: does the melted GAIN total match the wide-column total row we saw earlier?
    total_gain_prodk_sd = sd_long[(sd_long["product_label"].str.contains("Prod_K", na=False))
                                   & (sd_long["metric_type"] == "GAIN")]["value"].sum()
    print(f"\nSanity check — total 'Gains (cartons) Prod_K' across all SD rows: {total_gain_prodk_sd}")
