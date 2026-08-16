"""Behavioural tests for the landing writer. No Excel, no DuckLake extension needed."""
from __future__ import annotations

import pandas as pd
import pytest

from ingestion.load.landing_writer import LandingWriter, sha256_of


@pytest.fixture
def lake(tmp_path):
    # .db (not .ducklake) skips the DuckLake attach -- same code path otherwise.
    def _open(batch_id: str, **kwargs):
        return LandingWriter(
            tmp_path / "catalog.db", tmp_path / "storage", batch_id=batch_id, **kwargs
        )
    return _open


@pytest.fixture
def workbook(tmp_path):
    def _make(name: str, content: str):
        path = tmp_path / name
        path.write_text(content)
        return path
    return _make


def frame(sd_id, qtys, **extra):
    return pd.DataFrame({"sd_id": [sd_id] * len(qtys), "qty": qtys, **extra})


def test_append_stamps_provenance(lake, workbook):
    f = workbook("ExKP-Destocke_SD07.xlsx", "v1")
    with lake("B1") as w:
        w.append("kp_sd", frame("SD07", [10, 20]),
                 source_type="kp_sd", source_path=f, extractor="KPDestockeExtractor")
        rows = w.current_rows("kp_sd")
    assert len(rows) == 2
    assert set(rows["_batch_id"]) == {"B1"}
    assert set(rows["_source_file"]) == {"ExKP-Destocke_SD07.xlsx"}
    assert list(rows["_row_num"]) == [0, 1]


def test_corrected_file_supersedes_without_deleting_history(lake, workbook):
    """The point of append-only: the previous version stays queryable."""
    f = workbook("ExKP-Destocke_SD07.xlsx", "v1")
    with lake("B1") as w:
        w.append("kp_sd", frame("SD07", [10, 20]), source_type="kp_sd", source_path=f)

    f.write_text("v2-corrected")
    with lake("B2") as w:
        w.append("kp_sd", frame("SD07", [10, 25, 7]), source_type="kp_sd", source_path=f)
        assert len(w.current_rows("kp_sd")) == 3
        total = w.con.execute(f"SELECT COUNT(*) FROM {w.qschema}.kp_sd").fetchone()[0]
        assert total == 5, "history must be retained, not overwritten"


def test_other_files_are_unaffected_by_a_correction(lake, workbook):
    f1 = workbook("SD07.xlsx", "a")
    f2 = workbook("SD08.xlsx", "b")
    with lake("B1") as w:
        w.append("kp_sd", frame("SD07", [10]), source_type="kp_sd", source_path=f1)
        w.append("kp_sd", frame("SD08", [5]), source_type="kp_sd", source_path=f2)
    f1.write_text("a-fixed")
    with lake("B2") as w:
        w.append("kp_sd", frame("SD07", [11]), source_type="kp_sd", source_path=f1)
        current = w.current_rows("kp_sd")
    assert sorted(current["qty"]) == [5, 11]


def test_unchanged_file_is_detected_as_duplicate(lake, workbook):
    f = workbook("SD07.xlsx", "same")
    with lake("B1") as w:
        w.append("kp_sd", frame("SD07", [10]), source_type="kp_sd", source_path=f)
    with lake("B2") as w:
        assert w.already_ingested(sha256_of(f)) is True


def test_changed_file_is_not_a_duplicate(lake, workbook):
    f = workbook("SD07.xlsx", "before")
    with lake("B1") as w:
        w.append("kp_sd", frame("SD07", [10]), source_type="kp_sd", source_path=f)
    f.write_text("after")
    with lake("B2") as w:
        assert w.already_ingested(sha256_of(f)) is False


def test_new_source_column_evolves_the_table(lake, workbook):
    f = workbook("SD07.xlsx", "a")
    with lake("B1") as w:
        w.append("kp_sd", frame("SD07", [10]), source_type="kp_sd", source_path=f)
    f.write_text("b")
    with lake("B2") as w:
        w.append("kp_sd", frame("SD07", [10], sales_supervisor=["Awono"]),
                 source_type="kp_sd", source_path=f)
        assert "sales_supervisor" in w.current_rows("kp_sd").columns


def test_frozen_schema_rejects_a_new_column(lake, workbook):
    f = workbook("SD07.xlsx", "a")
    with lake("B1", evolve_schema=False) as w:
        w.append("kp_sd", frame("SD07", [10]), source_type="kp_sd", source_path=f)
    f.write_text("b")
    with lake("B2", evolve_schema=False) as w:
        with pytest.raises(ValueError, match="frozen schema"):
            w.append("kp_sd", frame("SD07", [10], extra=["x"]),
                     source_type="kp_sd", source_path=f)


def test_dropped_source_column_lands_null_not_error(lake, workbook):
    f = workbook("SD07.xlsx", "a")
    with lake("B1") as w:
        w.append("kp_sd", frame("SD07", [10], channel=["DG"]),
                 source_type="kp_sd", source_path=f)
    f.write_text("b")
    with lake("B2") as w:
        w.append("kp_sd", frame("SD07", [11]), source_type="kp_sd", source_path=f)
        assert w.current_rows("kp_sd")["channel"].isna().all()


def test_retired_file_drops_out_of_current(lake, workbook):
    f = workbook("SD08.xlsx", "a")
    with lake("B1") as w:
        w.append("kp_sd", frame("SD08", [5]), source_type="kp_sd", source_path=f)
    with lake("B2") as w:
        w.retire_file(str(f), "merged into regional workbook")
        assert len(w.current_rows("kp_sd")) == 0


def test_empty_frame_writes_nothing(lake, workbook):
    f = workbook("SD07.xlsx", "a")
    with lake("B1") as w:
        assert w.append("kp_sd", pd.DataFrame(), source_type="kp_sd", source_path=f) == 0


def test_writer_never_touches_the_warehouse_catalog(lake, workbook, tmp_path):
    """Guard on the core safety property of phase 1."""
    warehouse = tmp_path / "catalog.ducklake"
    warehouse.write_text("do not touch")
    before = warehouse.read_text()
    f = workbook("SD07.xlsx", "a")
    with lake("B1") as w:
        w.append("kp_sd", frame("SD07", [10]), source_type="kp_sd", source_path=f)
    assert warehouse.read_text() == before


# ── directory-frame path (what the ingestion asset actually calls) ──────────

def extractor_frame(rows, source_file, sheet="AWONO", table="Sales_Jan"):
    frame = pd.DataFrame(rows)
    frame["source_file"] = source_file
    frame["sheet_name"] = sheet
    frame["table_name"] = table
    frame["ingestion_ts"] = pd.Timestamp("2026-08-01")
    frame["ingestion_batch_id"] = "B0"
    return frame


@pytest.fixture
def input_dir(tmp_path):
    d = tmp_path / "input"
    d.mkdir()
    return d


def test_directory_frame_splits_by_source_file(lake, input_dir):
    (input_dir / "A.xlsx").write_text("a")
    (input_dir / "B.xlsx").write_text("b")
    df = pd.concat([
        extractor_frame([{"qty": 1}, {"qty": 2}], "A.xlsx"),
        extractor_frame([{"qty": 3}], "B.xlsx"),
    ], ignore_index=True)

    with lake("B1") as w:
        result = w.append_directory_frame(
            "sales_data", df, source_type="sales", input_dir=input_dir)
        assert result == {"A.xlsx": 2, "B.xlsx": 1}
        rows = w.current_rows("sales_data")
    # extractor provenance survives, renamed
    assert set(rows["_sheet_name"]) == {"AWONO"}
    assert set(rows["_table_name"]) == {"Sales_Jan"}
    assert "source_file" not in rows.columns, "must be renamed, not duplicated"


def test_unchanged_files_are_skipped_on_rerun(lake, input_dir):
    (input_dir / "A.xlsx").write_text("a")
    df = extractor_frame([{"qty": 1}], "A.xlsx")
    with lake("B1") as w:
        w.append_directory_frame("sales_data", df, source_type="sales", input_dir=input_dir)
    with lake("B2") as w:
        assert w.append_directory_frame(
            "sales_data", df, source_type="sales", input_dir=input_dir) == {}
        assert len(w.current_rows("sales_data")) == 1


def test_correcting_one_file_leaves_the_others_alone(lake, input_dir):
    (input_dir / "A.xlsx").write_text("a")
    (input_dir / "B.xlsx").write_text("b")
    with lake("B1") as w:
        w.append_directory_frame("sales_data", pd.concat([
            extractor_frame([{"qty": 1}], "A.xlsx"),
            extractor_frame([{"qty": 3}], "B.xlsx"),
        ], ignore_index=True), source_type="sales", input_dir=input_dir)

    (input_dir / "A.xlsx").write_text("a-corrected")
    with lake("B2") as w:
        w.append_directory_frame(
            "sales_data", extractor_frame([{"qty": 99}], "A.xlsx"),
            source_type="sales", input_dir=input_dir)
        rows = w.current_rows("sales_data")
        assert sorted(rows["qty"]) == [3, 99]
        total = w.con.execute(f"SELECT COUNT(*) FROM {w.qschema}.sales_data").fetchone()[0]
        assert total == 3, "the superseded row must remain in history"


def test_frame_without_source_file_is_rejected(lake, input_dir):
    with lake("B1") as w:
        with pytest.raises(ValueError, match="source_file"):
            w.append_directory_frame(
                "sales_data", pd.DataFrame({"qty": [1]}),
                source_type="sales", input_dir=input_dir)


# ── cross-file and cross-batch type conflicts ──────────────────────────────

def test_column_blank_in_first_file_accepts_text_from_second(lake, input_dir):
    """
    The ExKP-Destocke failure: sd_name is empty in the Centre workbook (the SD
    is encoded in the sheet name) and populated in the Nord one. Centre sorts
    first, so an all-null column created the table -- DuckDB typed it INTEGER
    and 'Appolinaire' could not be inserted.
    """
    (input_dir / "Centre.xlsx").write_text("c")
    (input_dir / "Nord.xlsx").write_text("n")

    frame = pd.concat([
        extractor_frame([{"qty": 1, "sd_name": None}], "Centre.xlsx"),
        extractor_frame([{"qty": 2, "sd_name": "Appolinaire"}], "Nord.xlsx"),
    ], ignore_index=True)
    # what BaseExcelExtractor now guarantees after its post-concat pass
    frame["sd_name"] = frame["sd_name"].astype("string")

    with lake("B1") as w:
        w.append_directory_frame("kp_sd", frame, source_type="kp_sd", input_dir=input_dir)
        rows = w.current_rows("kp_sd")
    assert sorted(rows["sd_name"].dropna()) == ["Appolinaire"]


def test_later_batch_with_an_incompatible_type_widens_the_column(lake, input_dir):
    """Cross-BATCH conflicts cannot be fixed in the extractor: widen to VARCHAR."""
    (input_dir / "A.xlsx").write_text("a")
    with lake("B1") as w:
        w.append_directory_frame(
            "kp_sd", extractor_frame([{"product_subcat": 0}], "A.xlsx"),
            source_type="kp_sd", input_dir=input_dir)

    (input_dir / "A.xlsx").write_text("a2")
    with lake("B2") as w:
        w.append_directory_frame(
            "kp_sd", extractor_frame([{"product_subcat": "Chocolat"}], "A.xlsx"),
            source_type="kp_sd", input_dir=input_dir)
        assert w.current_rows("kp_sd")["product_subcat"].tolist() == ["Chocolat"]
        total = w.con.execute(
            f"SELECT COUNT(*) FROM {w.qschema}.kp_sd").fetchone()[0]
        assert total == 2, "history kept, the old row now reads as text"


def test_numeric_widening_is_left_to_duckdb(lake, input_dir):
    """INTEGER then DOUBLE must not become VARCHAR."""
    (input_dir / "A.xlsx").write_text("a")
    with lake("B1") as w:
        w.append_directory_frame(
            "kp_sd", extractor_frame([{"weight": 2}], "A.xlsx"),
            source_type="kp_sd", input_dir=input_dir)

    (input_dir / "A.xlsx").write_text("a2")
    with lake("B2") as w:
        w.append_directory_frame(
            "kp_sd", extractor_frame([{"weight": 2.5}], "A.xlsx"),
            source_type="kp_sd", input_dir=input_dir)
        types = w._column_types("kp_sd")
    assert types["weight"] != "VARCHAR", f"weight became {types['weight']}"


def test_castable_type_difference_does_not_widen_the_column(lake, input_dir):
    """
    The regression that broke the real run: the table held unit_weight as
    DOUBLE, the batch brought VARCHAR, every value parsed. Comparing declared
    types and calling ALTER is wrong -- DuckLake permits widening promotions
    only, so DOUBLE -> VARCHAR is refused outright.
    """
    (input_dir / "A.xlsx").write_text("a")
    with lake("B1") as w:
        w.append_directory_frame(
            "sales", extractor_frame([{"unit_weight": 10.64}], "A.xlsx"),
            source_type="sales", input_dir=input_dir)

    (input_dir / "A.xlsx").write_text("a2")
    with lake("B2") as w:
        w.append_directory_frame(
            "sales", extractor_frame([{"unit_weight": "7.20"}], "A.xlsx"),
            source_type="sales", input_dir=input_dir)
        assert w._column_types("sales")["unit_weight"] == "DOUBLE"
        assert w.current_rows("sales")["unit_weight"].tolist() == [7.2]


def test_uncastable_value_widens_the_column(lake, input_dir):
    (input_dir / "A.xlsx").write_text("a")
    with lake("B1") as w:
        w.append_directory_frame(
            "sales", extractor_frame([{"unit_weight": 10.64}], "A.xlsx"),
            source_type="sales", input_dir=input_dir)

    (input_dir / "A.xlsx").write_text("a2")
    with lake("B2") as w:
        w.append_directory_frame(
            "sales", extractor_frame([{"unit_weight": "a peser"}], "A.xlsx"),
            source_type="sales", input_dir=input_dir)
        assert w._column_types("sales")["unit_weight"] == "VARCHAR"
        total = w.con.execute(f"SELECT COUNT(*) FROM {w.qschema}.sales").fetchone()[0]
        assert total == 2, "the pre-widening row must survive the rebuild"


def test_one_workbook_feeding_several_tables_is_not_deduped_away(lake, input_dir):
    """
    References.xlsx produces four landing tables. A hash-only duplicate check
    let the first table register the file and silently block the other three.
    """
    (input_dir / "References.xlsx").write_text("v1")
    frame = extractor_frame([{"sku": "CHOC001"}], "References.xlsx")

    with lake("B1") as w:
        for table in ("salesteam_data", "products_data", "clientsd_data"):
            assert w.append_directory_frame(
                table, frame, source_type="references",
                input_dir=input_dir, skip_duplicates=True,
            ) == {"References.xlsx": 1}, f"{table} was skipped"
        assert set(w.summary()["table"]) == {
            "salesteam_data", "products_data", "clientsd_data"}


def test_rerunning_the_same_workbook_still_skips_per_table(lake, input_dir):
    (input_dir / "References.xlsx").write_text("v1")
    frame = extractor_frame([{"sku": "CHOC001"}], "References.xlsx")
    with lake("B1") as w:
        w.append_directory_frame("products_data", frame, source_type="references",
                                 input_dir=input_dir, skip_duplicates=True)
    with lake("B2") as w:
        assert w.append_directory_frame(
            "products_data", frame, source_type="references",
            input_dir=input_dir, skip_duplicates=True) == {}


def test_column_blank_in_one_file_does_not_force_a_rebuild(lake, input_dir):
    """
    The extractor types sd_name as text across the directory, but this writer
    inserts one file at a time and DuckDB infers from the slice. Centre's slice
    is entirely null; it must not create an INTEGER column.
    """
    (input_dir / "Centre.xlsx").write_text("c")
    (input_dir / "Nord.xlsx").write_text("n")
    frame = pd.concat([
        extractor_frame([{"qty": 1, "sd_name": None}], "Centre.xlsx"),
        extractor_frame([{"qty": 2, "sd_name": "Appolinaire"}], "Nord.xlsx"),
    ], ignore_index=True)

    with lake("B1") as w:
        w.append_directory_frame("kp_sd", frame, source_type="kp_sd", input_dir=input_dir)
        assert w._column_types("kp_sd")["sd_name"] == "VARCHAR"
        assert sorted(w.current_rows("kp_sd")["sd_name"].dropna()) == ["Appolinaire"]