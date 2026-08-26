"""
reporting/utils/period.py

ONE object that answers "which rows belong to the selected period" for both
calendar years and the company's fiscal year.

    FY2026 = 2025-10-01 .. 2026-09-30

WHY THIS EXISTS
---------------
Every builder in queries.py used to filter with a bare `year = ?`. That is
correct only under a calendar basis. A fiscal year spans TWO calendar years, so
"FY2026" cannot be expressed as an equality on the `year` column at all -- it is

    (year = 2025 AND month >= 10) OR (year = 2026 AND month <= 9)

Putting that in one place, rather than in ~25 builders, is the whole point:
the fiscal boundary is a business rule, and a business rule copied 25 times is
a business rule that will disagree with itself.

NO SCHEMA CHANGE IS REQUIRED. The predicate is derived from the (year, month)
columns the bi views already expose, so this works whether or not
dim_date/v_*_kpi ever grow a `fiscal_year` column. If they do, this module is
the single place to switch over -- and switching over should be measured
against these predicates, not assumed equivalent.

BACKWARD COMPATIBILITY
----------------------
`Period.coerce()` accepts a plain int, so a page that has not been migrated
yet and still calls `get_monthly_trend(2026)` keeps working, on a calendar
basis, unchanged. Migration is page by page; nothing breaks in between.

⚠ A page that passes an int while the sidebar is set to a FISCAL basis will
report a CALENDAR year under a fiscal heading -- a wrong number that looks
right. Until every page passes `f["period"]`, treat the fiscal toggle as
trusted only on the pages listed in filters.py's MIGRATED note.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

# The fiscal year opens in October and is NAMED for the calendar year it ends
# in: October 2025 belongs to FY2026.
FISCAL_START_MONTH = 10

# Months in fiscal reading order, for labelling and sorting.
FISCAL_MONTH_ORDER: tuple[int, ...] = (10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9)

# Fiscal quarters. Kept here rather than in filters.py so the sidebar and the
# SQL cannot drift; filters.py imports this.
FISCAL_QUARTER_MONTHS: dict[int, list[int]] = {
    1: [10, 11, 12],
    2: [1, 2, 3],
    3: [4, 5, 6],
    4: [7, 8, 9],
}


def fiscal_year_of(year: int, month: int) -> int:
    """Which fiscal year does this calendar (year, month) fall in?"""
    return year + 1 if month >= FISCAL_START_MONTH else year


def fiscal_month_index(month: int) -> int:
    """1 for October .. 12 for September. Use for ordering, never for display."""
    return ((month - FISCAL_START_MONTH) % 12) + 1


@dataclass(frozen=True)
class Period:
    """A selected reporting period.

    year    calendar year when fiscal=False, fiscal year when fiscal=True
    months  calendar month numbers in scope, or None for the whole year
    fiscal  which basis `year` is expressed in
    """
    year: int
    months: tuple[int, ...] | None = None
    fiscal: bool = False

    # -- construction -------------------------------------------------------

    @classmethod
    def coerce(cls, value: "Period | int",
               month: int | None = None,
               months: Iterable[int] | None = None) -> "Period":
        """Accept a Period or a bare calendar year.

        `month`/`months` NARROW an existing Period, so a builder can keep its
        historic `(year, month)` signature and still be handed a Period:

            p = Period.coerce(year, month)
        """
        narrow: tuple[int, ...] | None = None
        if months is not None:
            narrow = tuple(int(m) for m in months)
        elif month is not None:
            narrow = (int(month),)

        if isinstance(value, Period):
            return value if narrow is None else Period(value.year, narrow, value.fiscal)
        return cls(int(value), narrow, False)

    def whole(self) -> "Period":
        """The same year with no month restriction -- for full-year trends."""
        return Period(self.year, None, self.fiscal)

    def prior(self) -> "Period":
        """The same shape, one year back. Used for every YoY comparison."""
        return Period(self.year - 1, self.months, self.fiscal)

    def narrow(self, months: Iterable[int] | None) -> "Period":
        return Period(self.year, tuple(months) if months else None, self.fiscal)

    # -- description --------------------------------------------------------

    @property
    def label(self) -> str:
        return f"FY{self.year}" if self.fiscal else str(self.year)

    def calendar_years(self) -> tuple[int, ...]:
        """Calendar years this period touches, ascending."""
        return tuple(sorted({y for y, _ in self._groups()}))

    def contains(self, year: int, month: int) -> bool:
        for y, ms in self._groups():
            if y == year and (ms is None or month in ms):
                return True
        return False

    # -- SQL ----------------------------------------------------------------

    def _groups(self) -> list[tuple[int, tuple[int, ...] | None]]:
        """(calendar_year, months) pairs covering the period.

        Calendar basis  -> one group.
        Fiscal basis    -> up to two, because a fiscal year straddles the
                           calendar boundary. Months are split by which side of
                           October they fall on, so a partial selection (one
                           month, one quarter) still lands in the right
                           calendar year -- January of FY2026 is 2026-01, while
                           November of FY2026 is 2025-11.
        """
        if not self.fiscal:
            return [(self.year, self.months)]

        if self.months is None:
            return [
                (self.year - 1, tuple(m for m in FISCAL_MONTH_ORDER
                                      if m >= FISCAL_START_MONTH)),
                (self.year, tuple(m for m in FISCAL_MONTH_ORDER
                                  if m < FISCAL_START_MONTH)),
            ]

        head = tuple(m for m in self.months if m >= FISCAL_START_MONTH)   # Oct-Dec
        tail = tuple(m for m in self.months if m < FISCAL_START_MONTH)    # Jan-Sep
        groups: list[tuple[int, tuple[int, ...] | None]] = []
        if head:
            groups.append((self.year - 1, head))
        if tail:
            groups.append((self.year, tail))
        return groups

    def clause(self, year_col: str = "year",
               month_col: str = "month") -> tuple[str, list]:
        """A parenthesised predicate plus its positional parameters.

        Always parenthesised: the fiscal form contains an OR, and dropping it
        into `WHERE ... AND <clause>` unparenthesised would silently widen the
        result to every row of one calendar year.
        """
        parts: list[str] = []
        params: list = []
        for cal_year, months in self._groups():
            if months is None:
                parts.append(f"{year_col} = ?")
                params.append(cal_year)
            else:
                holes = ",".join(["?"] * len(months))
                parts.append(f"({year_col} = ? AND {month_col} IN ({holes}))")
                params.append(cal_year)
                params.extend(int(m) for m in months)
        if len(parts) == 1:
            return f"({parts[0]})", params
        return "(" + " OR ".join(parts) + ")", params

    def order_expr(self, month_col: str = "month") -> str:
        """ORDER BY expression that puts months in reading order for the basis.

        Under a fiscal basis October must sort first, or a "monthly trend"
        chart draws the year starting in January and ending in December with
        the fiscal opening quarter tacked on the end.
        """
        if not self.fiscal:
            return month_col
        return f"((({month_col} - {FISCAL_START_MONTH} + 12) % 12) + 1)"

    def month_order(self) -> list[int]:
        """Months in reading order -- for sorting a DataFrame client-side."""
        if self.months is not None:
            source = list(self.months)
        else:
            source = list(range(1, 13))
        if not self.fiscal:
            return sorted(source)
        return sorted(source, key=fiscal_month_index)
