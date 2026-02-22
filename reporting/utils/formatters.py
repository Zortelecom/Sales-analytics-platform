"""
reporting/utils/formatters.py
Formatting helpers for numbers, currencies, and deltas.
"""
from __future__ import annotations
from reporting.config import CURRENCY_SYMBOL, COLORS, ACHIEVEMENT_THRESHOLDS


def fmt_currency(value: float | None, short: bool = False) -> str:
    """Format XAF currency. short=True → abbreviated (M/B)."""
    if value is None:
        return "—"
    if short:
        if abs(value) >= 1_000_000_000:
            return f"{value/1_000_000_000:.1f}B {CURRENCY_SYMBOL}"
        elif abs(value) >= 1_000_000:
            return f"{value/1_000_000:.1f}M {CURRENCY_SYMBOL}"
        elif abs(value) >= 1_000:
            return f"{value/1_000:.0f}K {CURRENCY_SYMBOL}"
    return f"{value:,.0f} {CURRENCY_SYMBOL}".replace(",", " ")


def fmt_number(value: float | None, decimals: int = 0) -> str:
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}".replace(",", " ")


def fmt_pct(value: float | None, decimals: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:.{decimals}f}%"


def fmt_delta(value: float | None, is_pct: bool = False) -> str:
    """Return a delta string with ▲/▼ prefix."""
    if value is None:
        return "—"
    sign = "▲" if value >= 0 else "▼"
    formatted = fmt_pct(abs(value)) if is_pct else fmt_number(abs(value))
    return f"{sign} {formatted}"


def achievement_color(pct: float | None) -> str:
    """Return a CSS color based on achievement %."""
    if pct is None:
        return COLORS["neutral"]
    if pct >= ACHIEVEMENT_THRESHOLDS["excellent"]:
        return COLORS["success"]
    elif pct >= ACHIEVEMENT_THRESHOLDS["good"]:
        return COLORS["accent"]
    elif pct >= ACHIEVEMENT_THRESHOLDS["warning"]:
        return COLORS["warning"]
    return COLORS["danger"]


def achievement_emoji(pct: float | None) -> str:
    if pct is None:
        return "⚪"
    if pct >= ACHIEVEMENT_THRESHOLDS["excellent"]:
        return "🟢"
    elif pct >= ACHIEVEMENT_THRESHOLDS["good"]:
        return "🟡"
    elif pct >= ACHIEVEMENT_THRESHOLDS["warning"]:
        return "🟠"
    return "🔴"


def month_name(month: int) -> str:
    names = ["Jan","Feb","Mar","Apr","May","Jun",
             "Jul","Aug","Sep","Oct","Nov","Dec"]
    return names[month - 1] if 1 <= month <= 12 else str(month)


def delta_style(value: float | None, inverse: bool = False) -> dict:
    """Return plotly delta reference properties."""
    if value is None:
        return {}
    positive_color = COLORS["success"] if not inverse else COLORS["danger"]
    negative_color = COLORS["danger"] if not inverse else COLORS["success"]
    return {
        "increasing": {"color": positive_color},
        "decreasing": {"color": negative_color},
    }
