"""
reporting/theme.py

Single source of truth for `st.set_page_config()` and the global CSS.

WHY THIS EXISTS
---------------
Previously both lived inline in app.py. When app.py crashed on import (see
the ModuleNotFoundError below), Streamlit fell back to its *legacy* multipage
mode and ran `reporting/pages/*.py` directly as standalone scripts. Those
scripts never call `set_page_config(layout="wide")` and never inject the CSS,
which is exactly why the report rendered narrow and unstyled.

Keeping both here means any entry point can call `configure_page()` +
`inject_css()` idempotently.
"""
from __future__ import annotations

import streamlit as st

from reporting.config import APP_TITLE, APP_ICON, COLORS

# Maximum width of the main content column.
# Was hard-coded to 1400px, which on a 2560px monitor left ~1100px of empty
# gutter. "none" lets the content use the full viewport; set a px value if you
# prefer a reading-width cap for text-heavy pages.
CONTENT_MAX_WIDTH = "none"


def configure_page(page_title: str | None = None) -> None:
    """Call `st.set_page_config` exactly once per session, safely.

    `set_page_config` must be the first Streamlit call in a script run and
    raises StreamlitAPIException if called twice. Under `st.navigation` the
    page scripts run inside app.py's script run, so app.py owns this call --
    but the try/except keeps a directly-executed page from hard-crashing.
    """
    try:
        st.set_page_config(
            page_title=page_title or APP_TITLE,
            page_icon=APP_ICON,
            layout="wide",
            initial_sidebar_state="expanded",
        )
    except Exception:
        # Already configured in this script run -- nothing to do.
        pass


def inject_css() -> None:
    """Inject the global stylesheet AND reset the per-run render registries.

    The reset is bundled here deliberately. It has to happen exactly once per
    script run, before any page code executes, and `inject_css()` is already
    the one call guaranteed to satisfy that -- app.py runs it at the top of
    every rerun, before `pg.run()`.

    Getting this wrong is worse than not having the registries at all: without
    a reset, `claim_once()` would suppress a page header on every run after
    the first, so the header would appear once and then vanish the moment you
    touched a filter.
    """
    from reporting.utils.render import reset_render_state

    reset_render_state()
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)


_GLOBAL_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=DM+Mono:wght@400;500&display=swap');

/* ---- Global ---- */
html, body, [class*="css"] {{
    font-family: 'DM Sans', sans-serif;
    background-color: {COLORS['bg_primary']} !important;
    color: {COLORS['text_primary']};
}}

/* ---- Main area ----
   layout="wide" removes Streamlit's own centred column, but the app still
   caps itself here. CONTENT_MAX_WIDTH = "none" -> genuinely full width. */
.main .block-container {{
    padding: 1.5rem 2rem 2rem 2rem;
    max-width: {CONTENT_MAX_WIDTH};
}}

/* ---- Headers ---- */
h1, h2, h3, h4 {{ color: {COLORS['text_primary']}; }}

/* ---- Metric boxes (Streamlit native) ---- */
[data-testid="stMetric"] {{
    background: {COLORS['bg_card']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    padding: 0.75rem 1rem;
}}
[data-testid="stMetricLabel"] {{ color: {COLORS['text_secondary']} !important; font-size: 0.78rem !important; }}
[data-testid="stMetricValue"] {{ color: {COLORS['text_primary']} !important; font-size: 1.4rem !important; }}
[data-testid="stMetricDelta"] {{ font-size: 0.78rem !important; }}

/* ---- Selectboxes ---- */
.stSelectbox > div > div {{
    background: {COLORS['bg_card']} !important;
    border-color: {COLORS['border']} !important;
    color: {COLORS['text_primary']} !important;
}}
.stMultiSelect > div {{
    background: {COLORS['bg_card']} !important;
    border-color: {COLORS['border']} !important;
}}
.stMultiSelect [data-baseweb="tag"] {{
    background: {COLORS['accent']} !important;
    color: #000 !important;
}}

/* ---- Tabs ---- */
.stTabs [data-baseweb="tab-list"] {{
    background: {COLORS['bg_card']};
    border-radius: 8px 8px 0 0;
    border-bottom: 1px solid {COLORS['border']};
    gap: 0;
}}
.stTabs [data-baseweb="tab"] {{
    background: transparent;
    color: {COLORS['text_secondary']};
    border-radius: 0;
    font-size: 0.85rem;
    padding: 0.6rem 1.2rem;
}}
.stTabs [aria-selected="true"] {{
    background: {COLORS['bg_card_alt']} !important;
    color: {COLORS['accent']} !important;
    border-bottom: 2px solid {COLORS['accent']} !important;
    font-weight: 600;
}}
.stTabs [data-baseweb="tab-panel"] {{
    background: {COLORS['bg_card']};
    border: 1px solid {COLORS['border']};
    border-top: none;
    border-radius: 0 0 8px 8px;
    padding: 1.25rem;
}}

/* ---- Dividers ---- */
hr {{ border-color: {COLORS['border']}; }}

/* ---- Dataframes ---- */
.stDataFrame {{
    background: {COLORS['bg_card']};
    border: 1px solid {COLORS['border']};
}}

/* ---- Radio / Checkbox ---- */
.stRadio [data-testid="stMarkdownContainer"] p {{
    color: {COLORS['text_secondary']};
    font-size: 0.85rem;
}}

/* ---- Page nav ----
   REMOVED: `[data-testid="stSidebarNav"] {{ display: none; }}`

   That rule was almost certainly added to hide Streamlit's *legacy* auto-
   discovered page list. But `st.navigation()` renders into the SAME
   container, so the rule was also hiding the real navigation -- which is why
   the page list vanished whenever app.py actually managed to run. The legacy
   list is now gone for good because `reporting/pages/` has been renamed to
   `reporting/app_pages/`, so there is nothing left to hide. */
[data-testid="stSidebarNav"] {{
    padding-top: 0.5rem;
}}
[data-testid="stSidebarNav"] a span {{
    font-size: 0.88rem;
}}

/* ---- Scrollbar ---- */
::-webkit-scrollbar {{ width: 6px; height: 6px; }}
::-webkit-scrollbar-track {{ background: {COLORS['bg_primary']}; }}
::-webkit-scrollbar-thumb {{ background: {COLORS['border']}; border-radius: 3px; }}

/* ---- Column gaps ---- */
[data-testid="column"] {{ gap: 0.75rem; }}

/* ---- Info/warning boxes ---- */
.stAlert {{ background: {COLORS['bg_card_alt']}; border-radius: 6px; }}

/* ---- Spacing utility ---- */
.spacer-sm {{ margin-top: 0.5rem; }}
.spacer-md {{ margin-top: 1rem; }}
.spacer-lg {{ margin-top: 1.5rem; }}

/* ---- Safety net: any raw <table> built with st.markdown(unsafe_allow_html=True)
   gets horizontal scroll instead of overflowing on narrow screens. ---- */
.main .stMarkdown table,
.main [data-testid="stMarkdownContainer"] table,
.main [data-testid="stHtml"] table,
.main .stHtml table {{
    display: block;
    overflow-x: auto;
    white-space: nowrap;
}}

/* ---- Login card ---- */
.login-wrap {{
    max-width: 420px;
    margin: 4rem auto 0 auto;
}}

/* =====================================================================
   Responsive breakpoints
   ===================================================================== */
@media (max-width: 768px) {{
    .main .block-container {{
        padding: 0.75rem 0.85rem 1.25rem 0.85rem;
    }}
    h1 {{ font-size: 1.35rem !important; }}
    h2 {{ font-size: 1.1rem !important; }}
    h3, h4 {{ font-size: 0.95rem !important; }}
    [data-testid="stMetric"] {{ padding: 0.6rem 0.75rem; }}
    [data-testid="stMetricValue"] {{ font-size: 1.15rem !important; }}
    [data-testid="stMetricLabel"] {{ font-size: 0.7rem !important; }}
    .stTabs [data-baseweb="tab"] {{
        padding: 0.45rem 0.6rem;
        font-size: 0.75rem;
    }}
    .stTabs [data-baseweb="tab-panel"] {{ padding: 0.75rem; }}
    .main .stMarkdown table,
    .main [data-testid="stMarkdownContainer"] table,
    .main [data-testid="stHtml"] table,
    .main .stHtml table {{ font-size: 0.72rem; }}
}}
</style>
"""