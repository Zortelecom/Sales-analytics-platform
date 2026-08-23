"""
reporting/app.py
Main entry point for the Sales Analytics Platform dashboard.

    streamlit run reporting/app.py        (from the project root)

WHAT WAS BROKEN
---------------
`streamlit run reporting/app.py` puts *reporting/* on sys.path, not the
project root. So line 1 of the old app.py -- `import reporting._bootstrap` --
raised ModuleNotFoundError before anything else ran. The page files worked
because each one inserts the project root into sys.path *before* importing
the package; app.py itself had no such guard.

Two things followed from that crash:

  * `st.set_page_config(layout="wide")` and the global CSS never ran, so
    whichever page you clicked rendered at Streamlit's default centred width
    and unstyled -- the narrow layout in Screenshot 135945.

  * `st.navigation()` never ran either, so Streamlit fell back to legacy
    multipage mode and auto-discovered `reporting/pages/*.py`. That is where
    the phantom "app" entry at the top of the sidebar came from: in legacy
    mode the entry-point script becomes the first nav item, and clicking it
    re-ran the crashing app.py.

  * Meanwhile the CSS contained `[data-testid="stSidebarNav"] { display:none }`
    -- presumably to hide that legacy list. But `st.navigation()` renders into
    the same container, so on the rare run where app.py *did* work you got a
    wide layout with no navigation at all (Screenshot 140033).

THE FIX
-------
  * sys.path is fixed inline, before any `reporting.*` import.
  * `reporting/pages/` is renamed to `reporting/app_pages/`, which removes
    Streamlit's legacy auto-discovery entirely. `st.navigation` is now the
    only navigation, and the `display:none` rule is gone from theme.py.
  * Page config + CSS moved to reporting/theme.py so any entry point gets them.
  * Auth gate runs before `pg.run()`; with legacy discovery gone there is no
    URL that reaches a page without passing it.
"""
# --- path bootstrap: must come before any `reporting.*` import ---------------
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
# ----------------------------------------------------------------------------

import streamlit as st  # noqa: E402

from reporting.theme import configure_page, inject_css  # noqa: E402

# set_page_config must be the first Streamlit call in the script run.
configure_page()
inject_css()

from reporting.auth.session import login_gate, render_user_badge  # noqa: E402
from reporting.auth.users import AuthConfigError  # noqa: E402
from reporting.utils.render import reset_render_state  # noqa: E402

# Clear the per-run widget-key counters and header registry. Must happen once
# per script run, before any page code executes, so download-button keys stay
# deterministic across reruns instead of drifting upward.
reset_render_state()

try:
    principal = login_gate()          # halts the script until signed in
except AuthConfigError as exc:
    st.error(str(exc))
    st.stop()

render_user_badge(principal)


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
# (page_key, path, title, icon, extra_condition)
_ALL_PAGES = [
    ("executive", "app_pages/1_Executive_Overview.py",   "Executive Overview",   "🏠", True),
    ("regional",  "app_pages/2_Regional_Performance.py", "Regional Performance", "🗺️", True),
    ("salesforce", "app_pages/3_Salesforce_Performance.py", "Salesforce Performance", "👥", True),
    ("product",   "app_pages/4_Product_Performance.py",  "Product Performance",  "📦", True),
    ("time",      "app_pages/5_Time_Intelligence.py",    "Time Intelligence",    "⏱️", True),
    ("sellin",    "app_pages/6_Sell_In_Sell_Out.py",     "Sell-In / Sell-Out",   "🔄", True),
    # Free-form SQL is the one page RLS cannot make *safe* on its own -- the
        # rewriter scopes the rows, but a curious user can still enumerate the
        # schema and probe. Gate it on an explicit per-user flag.
    ("ask",       "app_pages/7_Ask_Data.py",             "Ask Your Data",        "💬",
        principal.can_ask_data),
    ("quality",   "app_pages/8_Pipeline_Health.py",       "Data Quality",         "🔍", True),
    
]

pages = [
    st.Page(path, title=title, icon=icon)
    for key, path, title, icon, allowed in _ALL_PAGES
    if allowed and principal.allows_page(key)
]

if not pages:
    st.error("Your account has no pages assigned. Contact the report owner.")
    st.stop()

pg = st.navigation(pages)
pg.run()