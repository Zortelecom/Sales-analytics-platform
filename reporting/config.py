"""
reporting/config.py
Central configuration for the Sales Analytics reporting app.
"""
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.environ.get(
    "SERVING_DB_PATH",
    str(PROJECT_ROOT / "data" / "warehouse" / "serving_dev.db"),
)

# ---------------------------------------------------------------------------
# Database schema names
# ---------------------------------------------------------------------------
# Schema where your dim/fact tables live in serving.db  (e.g. "bi", "main")
# Override at runtime:  set SERVING_DB_SCHEMA=main
DB_SCHEMA = os.environ.get("SERVING_DB_SCHEMA", "bi")

# Schema where the reporting KPI views (v_monthly_kpi etc.) are created.
# "main" = default/unqualified schema in DuckDB — usually fine.
DB_VIEWS_SCHEMA = os.environ.get("SERVING_DB_VIEWS_SCHEMA", "main")

# ---------------------------------------------------------------------------
# App metadata
# ---------------------------------------------------------------------------
APP_TITLE = "Sales Analytics Platform"
APP_ICON = "📊"
COMPANY_NAME = os.environ.get("COMPANY_NAME", "Sales Corp")
CURRENCY = "XAF"
CURRENCY_SYMBOL = "FCFA"

# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------
DEFAULT_YEAR = None
DEFAULT_MEETING_TYPE = "Monthly"
MEETING_TYPES = ["Weekly", "Monthly", "Quarterly", "Annual"]

THOUSANDS_SEP = " "
DECIMAL_SEP = "."

# ---------------------------------------------------------------------------
# Colors — design system
# ---------------------------------------------------------------------------
COLORS = {
    "bg_primary":    "#0A0E1A",
    "bg_card":       "#111827",
    "bg_card_alt":   "#1A2235",
    "accent":        "#F59E0B",
    "accent_light":  "#FCD34D",
    "success":       "#10B981",
    "warning":       "#F97316",
    "danger":        "#EF4444",
    "neutral":       "#6B7280",
    "text_primary":  "#F9FAFB",
    "text_secondary":"#9CA3AF",
    "border":        "#1F2937",
    "chart": [
        "#F59E0B", "#10B981", "#3B82F6", "#8B5CF6",
        "#EC4899", "#14B8A6", "#F97316", "#6366F1",
    ],
}

# ---------------------------------------------------------------------------
# Achievement thresholds
# ---------------------------------------------------------------------------
ACHIEVEMENT_THRESHOLDS = {
    "excellent": 100,
    "good":       85,
    "warning":    70,
}