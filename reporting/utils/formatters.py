"""
reporting/utils/formatters.py
Formatting helpers for numbers, currencies, and deltas.

Fixes applied:
  - Added wow_color(pct): growth-appropriate colour scale for WoW %
    (replaces the misuse of achievement_color(pct+100) in page 5)
"""
from __future__ import annotations
import math
from reporting.config import CURRENCY_SYMBOL, COLORS, ACHIEVEMENT_THRESHOLDS

def _is_null(value) -> bool:
    """True for Python None AND float/pandas NaN."""
    if value is None:
        return True
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def fmt_currency(value: float | None, short: bool = False) -> str:
    """Format XAF currency. short=True → abbreviated (M/B)."""
    if _is_null(value):
        return "—"
    if short:
        if abs(value) >= 1_000_000_000:
            return f"{value/1_000_000_000:.1f}B {CURRENCY_SYMBOL}"
        elif abs(value) >= 1_000_000:
            return f"{value/1_000_000:.1f}M {CURRENCY_SYMBOL}"
        elif abs(value) >= 1_000:
            return f"{value/1_000:.0f}K {CURRENCY_SYMBOL}"
    return f"{value:,.0f} {CURRENCY_SYMBOL}".replace(",", "\u00a0")


def fmt_number(value, decimals: int = 0) -> str:
    if _is_null(value):
        return "—"
    return f"{float(value):,.{decimals}f}".replace(",", "\u00a0")


def fmt_pct(value, decimals: int = 1) -> str:
    if _is_null(value):
        return "—"
    return f"{float(value):.{decimals}f}%"


def fmt_delta(value, is_pct: bool = False) -> str:
    """Return a delta string with ▲/▼ prefix."""
    if _is_null(value):
        return "—"
    value = float(value)
    sign = "▲" if value >= 0 else "▼"
    formatted = fmt_pct(abs(value)) if is_pct else fmt_number(abs(value))
    return f"{sign} {formatted}"


def achievement_color(pct) -> str:
    """Return a CSS color based on target achievement %. NaN-safe.
    Thresholds: excellent ≥ 100 %, good ≥ 85 %, warning ≥ 70 %, else danger.
    Use wow_color() for week-over-week growth percentages.
    """
    if _is_null(pct):
        return COLORS["neutral"]
    pct = float(pct)
    if pct >= ACHIEVEMENT_THRESHOLDS["excellent"]:
        return COLORS["success"]
    elif pct >= ACHIEVEMENT_THRESHOLDS["good"]:
        return COLORS["accent"]
    elif pct >= ACHIEVEMENT_THRESHOLDS["warning"]:
        return COLORS["warning"]
    return COLORS["danger"]


def wow_color(pct) -> str:
    """Return a CSS color for week-over-week growth percentage. NaN-safe.
    Uses growth-appropriate thresholds distinct from target-achievement ones:
      ≥ 5 %  → success (strong growth)
      ≥ 0 %  → accent  (flat / slight growth)
      ≥ -5 % → warning (slight decline)
      < -5 % → danger  (significant decline)
    """
    if _is_null(pct):
        return COLORS["neutral"]
    pct = float(pct)
    if pct >= 5:
        return COLORS["success"]
    elif pct >= 0:
        return COLORS["accent"]
    elif pct >= -5:
        return COLORS["warning"]
    return COLORS["danger"]


def achievement_emoji(pct) -> str:
    if _is_null(pct):
        return "⚪"
    pct = float(pct)
    if pct >= ACHIEVEMENT_THRESHOLDS["excellent"]:
        return "🟢"
    elif pct >= ACHIEVEMENT_THRESHOLDS["good"]:
        return "🟡"
    elif pct >= ACHIEVEMENT_THRESHOLDS["warning"]:
        return "🟠"
    return "🔴"


def month_name(month) -> str:
    names = ["Jan","Feb","Mar","Apr","May","Jun",
             "Jul","Aug","Sep","Oct","Nov","Dec"]
    try:
        return names[int(month) - 1]
    except (ValueError, TypeError, IndexError):
        return str(month)


def delta_style(value, inverse: bool = False) -> dict:
    if _is_null(value):
        return {}
    positive_color = COLORS["success"] if not inverse else COLORS["danger"]
    negative_color = COLORS["danger"] if not inverse else COLORS["success"]
    return {
        "increasing": {"color": positive_color},
        "decreasing": {"color": negative_color},
    }
