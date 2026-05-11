"""
reporting/app.py
Main entry point for the Sales Analytics Platform dashboard.
Run with: streamlit run reporting/app.py
"""

import reporting._bootstrap  # noqa: F401 — must be first; adds project root to sys.path
import streamlit as st
from reporting.config import APP_TITLE, APP_ICON, COLORS


# ---------------------------------------------------------------------------
# Page config — must be first Streamlit call
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title=APP_TITLE,
    page_icon=APP_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# Global CSS injection — dark executive theme
# ---------------------------------------------------------------------------
st.markdown(
    f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=DM+Mono:wght@400;500&display=swap');

    /* ---- Global ---- */
    html, body, [class*="css"] {{
        font-family: 'DM Sans', sans-serif;
        background-color: {COLORS['bg_primary']} !important;
        color: {COLORS['text_primary']};
    }}

    /* ---- Main area ---- */
    .main .block-container {{
        padding: 1.5rem 2rem 2rem 2rem;
        max-width: 1400px;
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

    /* ---- Page nav ---- */
    [data-testid="stSidebarNav"] {{ display: none; }}

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
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Navigation definition
# ---------------------------------------------------------------------------
pages = [
    st.Page("pages/1_Executive_Overview.py",   title="Executive Overview",      icon="🏠"),
    st.Page("pages/2_Regional_Performance.py", title="Regional Performance",    icon="🗺️"),
    st.Page("pages/3_Salesforce_Performance.py", title="Salesforce Performance", icon="👥"),
    st.Page("pages/4_Product_Performance.py",  title="Product Performance",     icon="📦"),
    st.Page("pages/5_Time_Intelligence.py",    title="Time Intelligence",       icon="⏱️"),
]

pg = st.navigation(pages)
pg.run()