"""
diagnose_double_header.py

Drop this next to app.py (i.e. at `reporting/diagnose_double_header.py`) and
add ONE line at the very top of any affected page, above every other import:

    import reporting.diagnose_double_header   # noqa: F401  TEMPORARY

Reload the page, then read the banner it prints at the top of the main area.

WHY THIS IS NEEDED
------------------
A header showing up twice has exactly two possible causes, and they need
different fixes:

  A. The page body executes twice inside one script run.
     -> the banner appears TWICE, with two different timestamps.
     -> `claim_once()` in render_page_header fixes it.

  B. The page body executes once, but Streamlit's frontend keeps the previous
     run's element and appends the new one instead of replacing it. This
     happens when the number of elements earlier in the run changes between
     runs -- an st.error that shows on one run and not the next, a login form
     that renders once and then goes away, a spinner-guarded block that
     short-circuits.
     -> the banner appears ONCE but the header still appears twice.
     -> `keyed_container()` in render_page_header fixes it.

Both fixes are already in place, so the symptom should be gone either way.
This tells you which one was actually doing the work -- worth knowing, because
cause B tends to come back somewhere else in the app.

STRONG HINT IT IS CAUSE B
-------------------------
If the page body ran twice, `render_sidebar_filters()` would run twice too,
and its widgets carry explicit keys (`filter_year`, `filter_regions`, ...).
Streamlit raises StreamlitDuplicateElementKey on the second one. You've been
getting that error for download buttons but never for the sidebar filters,
which says the body runs once. That points at B.

REMEMBER TO REMOVE THE IMPORT once you've read the answer.
"""
from __future__ import annotations

import time

import streamlit as st

_COUNTER = "_diag_body_executions"

# Count executions within this script run. reset_render_state() (called from
# theme.inject_css) does not touch this key, so we detect a new run by
# comparing against the wall clock instead.
_now = time.time()
_hist: list[float] = st.session_state.setdefault(_COUNTER, [])
_hist = [t for t in _hist if _now - t < 2.0]     # anything older is a prior run
_hist.append(_now)
st.session_state[_COUNTER] = _hist

_n = len(_hist)
_colour = "#10B981" if _n == 1 else "#EF4444"
_verdict = (
    "Body executed ONCE this run. If the header still appears twice, the cause "
    "is element-tree duplication in the frontend (cause B) -- keyed_container "
    "is what fixes it."
    if _n == 1 else
    f"Body executed {_n} TIMES this run. Something is running the page script "
    "more than once (cause A) -- claim_once is what fixes it. Check for a "
    "leftover reporting/pages/ directory next to app.py, and for the page "
    "being listed twice in _ALL_PAGES."
)

st.markdown(
    f'<div style="border:1px solid {_colour}; border-left:4px solid {_colour}; '
    f'background:#111827; border-radius:6px; padding:0.6rem 0.9rem; '
    f'margin-bottom:0.75rem; font-size:0.8rem; color:#F9FAFB;">'
    f'<b>DIAGNOSTIC</b> — execution #{_n} at {_now:.3f}<br>{_verdict}</div>',
    unsafe_allow_html=True,
)
