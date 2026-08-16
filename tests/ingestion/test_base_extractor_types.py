"""
Type handling in BaseExcelExtractor.

test_missing_values_become_null is the important one: the previous
`astype(str)` rendered a blank cell as the string "nan", so every downstream
`IS NOT NULL` guard silently passed those rows through.
"""
from __future__ import annotations

import datetime as dt

import duckdb
import pandas as pd
import pytest

from ingestion.extract.base_extractor import (
    BaseExcelExtractor,
    normalize_header,
)


@pytest.fixture
def extractor():
    return BaseExcelExtractor("B1")


def duck_types(frame: pd.DataFrame) -> dict:
    con = duckdb.connect()
    con.register("t", frame)
    return {row[0]: row[1] for row in con.execute("DESCRIBE SELECT * FROM t").fetchall()}


@pytest.mark.parametrize("raw,position,expected", [
    ("  SD_ID ", 0, "sd_id"),
    ("Sale  Date", 1, "sale date"),
    (None, 2, "col_2"),
    (0, 3, "0"),            # a header cell of 0 is a header, not a blank
    ("", 4, "col_4"),
])
def test_header_normalisation(raw, position, expected):
    assert normalize_header(raw, position) == expected


def test_missing_values_become_null_not_the_string_nan(extractor):
    frame = extractor._normalize_types(
        pd.DataFrame({"sku": ["ABC", None], "sale_date": [dt.datetime(2025, 12, 1), None]}),
        "test",
    )
    con = duckdb.connect()
    con.register("t", frame)
    nulls = con.execute(
        "SELECT COUNT(*) FROM t WHERE sku IS NULL AND sale_date IS NULL"
    ).fetchone()[0]
    assert nulls == 1
    assert "nan" not in {str(v) for v in frame["sku"].tolist()}


def test_clean_types_are_preserved(extractor):
    frame = extractor._normalize_types(
        pd.DataFrame({
            "sku": ["ABC", "DEF"],
            "sale_date": [dt.datetime(2025, 12, 1), dt.datetime(2025, 12, 2)],
            "weight": [1.5, 2.25],
            "is_innovation": [True, False],
        }),
        "test",
    )
    types = duck_types(frame)
    assert types["sku"] == "VARCHAR"
    assert types["sale_date"].startswith("TIMESTAMP")
    assert types["weight"] == "DOUBLE"
    assert types["is_innovation"] == "BOOLEAN"


def test_integer_column_with_a_blank_cell_stays_integer(extractor):
    """pandas degrades int to float on NaN; XAF amounts and quantities are integers."""
    frame = extractor._normalize_types(pd.DataFrame({"qty": [10, 20, None]}), "test")
    assert duck_types(frame)["qty"] in {"BIGINT", "INTEGER"}
    con = duckdb.connect()
    con.register("t", frame)
    assert con.execute("SELECT qty FROM t ORDER BY qty").fetchall() == [(10,), (20,), (None,)]


def test_int_float_mix_widens_to_numeric_not_string(extractor):
    """2 and 2.5 in one unit_weight column is formatting, not a type conflict."""
    frame = extractor._normalize_types(
        pd.DataFrame({"unit_weight": [0.4, 1, 0.25, 2]}), "test")
    assert duck_types(frame)["unit_weight"] == "DOUBLE"
    assert extractor.coerced_columns == {}, "must not be reported as coerced"


def test_whole_float_column_stays_integer(extractor):
    """XAF has no subunit; an amount column must not land as DOUBLE."""
    frame = extractor._normalize_types(
        pd.DataFrame({"amount": [1500.0, 2000.0, None]}), "test")
    assert duck_types(frame)["amount"] in {"BIGINT", "INTEGER"}


def test_date_datetime_mix_widens_to_timestamp(extractor):
    frame = extractor._normalize_types(
        pd.DataFrame({"sale_date": [
            dt.date(2025, 12, 1), dt.datetime(2025, 12, 2), None]}), "test")
    assert duck_types(frame)["sale_date"].startswith("TIMESTAMP")
    assert extractor.coerced_columns == {}


def test_all_empty_column_is_text_not_integer(extractor):
    """
    DuckDB guesses INTEGER for an all-None object column, and the next file
    with a real value then fails with "Could not convert string 'X' to INT32".
    """
    frame = extractor._normalize_types(pd.DataFrame({"sd_name": [None, None]}), "test")
    assert duck_types(frame)["sd_name"] == "VARCHAR"


def test_mixed_type_column_falls_back_to_string_and_is_recorded(extractor):
    """Only when a value genuinely is not a number."""
    frame = extractor._normalize_types(
        pd.DataFrame({"amount": [1500, "a confirmer", 700]}), "ExKP.xlsx/SD07/SalesIn"
    )
    assert duck_types(frame)["amount"] == "VARCHAR"
    assert extractor.coerced_columns["amount"] == {"int", "str"}


def test_numbers_stored_as_text_are_parsed_not_stringified(extractor):
    """
    unit_weight: text throughout some workbooks, float in others, every value
    parseable. Stringifying it pushes a CAST into staging and makes arithmetic
    on it silently wrong.
    """
    frame = extractor._normalize_types(
        pd.DataFrame({"unit_weight": ["9", 10, 10.64, "7.20", None]}),
        "sales (all files)",
    )
    assert duck_types(frame)["unit_weight"] == "DOUBLE"
    assert extractor.coerced_columns == {}, "must not be reported as coerced"


def test_french_thousands_and_decimal_separators_parse(extractor):
    frame = extractor._normalize_types(
        pd.DataFrame({"amount": [1500, "1\u00a0500", "2 000", "7,20"]}), "test")
    assert duck_types(frame)["amount"] == "DOUBLE"


def test_zero_padded_identifiers_are_not_turned_into_numbers(extractor):
    """"007" is an identifier; parsing it to 7 loses the padding."""
    frame = extractor._normalize_types(
        pd.DataFrame({"code": [7, "007", "012", 9]}), "test")
    assert duck_types(frame)["code"] == "VARCHAR"


def test_ambiguous_comma_grouping_is_refused(extractor):
    """"1,500" alone could be 1.5 or 1500 — report it rather than guess."""
    frame = extractor._normalize_types(
        pd.DataFrame({"amount": [12.5, "1,500x"]}), "test")
    assert duck_types(frame)["amount"] == "VARCHAR"


def test_boolean_column_is_not_stringified_by_a_zero(extractor):
    frame = extractor._normalize_types(pd.DataFrame({"flag": [True, False, 0]}), "test")
    assert extractor.coerced_columns == {}


def test_whitespace_is_stripped_from_strings(extractor):
    frame = extractor._normalize_types(pd.DataFrame({"sku": ["  ABC ", "DEF"]}), "test")
    assert frame["sku"].tolist() == ["ABC", "DEF"]


def test_whitespace_only_cell_is_missing_not_empty_string(extractor):
    frame = extractor._normalize_types(pd.DataFrame({"sku": ["ABC", "   "]}), "test")
    assert frame["sku"].tolist() == ["ABC", None]


def test_opt_in_string_coercion_still_available(extractor):
    extractor.COERCE_ALL_TO_STRING = True
    frame = extractor._normalize_types(pd.DataFrame({"qty": [10, None]}), "test")
    assert frame["qty"].tolist() == ["10", None], "None must survive even here"