"""
reporting/_bootstrap.py
Ensures the project root is on sys.path regardless of how/where
Streamlit launches the app. Import this as the FIRST thing in every
reporting file (app.py, pages/*, utils/*, components/*).

Usage:
    import reporting._bootstrap  # noqa: F401  (side-effect only)
"""
import sys
from pathlib import Path

# reporting/_bootstrap.py  →  .parent = reporting/  →  .parent = project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
