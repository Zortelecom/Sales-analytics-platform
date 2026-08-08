"""
reporting/utils/render.py

Two things every page needs and neither of which Streamlit gives you directly.

1. `html_block()` — render raw HTML without Markdown mangling it.

   `st.markdown(html, unsafe_allow_html=True)` runs the string through a
   Markdown parser first. Markdown's indented-code-block rule says any line
   indented four or more spaces is a code block, and Streamlit only strips the
   *common* indent via textwrap.dedent. So HTML built inside a nested
   f-string -- a `<tr>` assembled in a `for` loop inside a `with` block inside
   a tab -- keeps its extra indentation and gets printed as source instead of
   rendered. That is the `<tr style="border-bottom:1px solid #1F29` you can see
   in the Product Detail Table, the Weekly Breakdown and the Regional SMLY
   panels.

   `st.html()` (Streamlit >= 1.33) skips the Markdown parser entirely, so
   indentation is irrelevant. `html_block()` prefers it and falls back to a
   dedented `st.markdown` on older versions.

2. `unique_key()` — collision-free widget keys within one script run.

   `st.download_button(key=f"dl_{file_name}")` derived its key only from the
   column name, so two ranking tables over the same column in different tabs
   produced the same key and Streamlit raised StreamlitDuplicateElementKey.
   A run-scoped counter disambiguates them without pages having to invent
   names by hand.

   `reset_render_state()` must be called once at the top of every script run
   (app.py does this) so the counters restart and keys stay stable across
   reruns rather than drifting upward forever.
"""
from __future__ import annotations

import textwrap

import streamlit as st

_SEQ_KEY = "_render_seq"
_HEADER_KEY = "_render_headers"


# ---------------------------------------------------------------------------
# Run-scoped state
# ---------------------------------------------------------------------------

def reset_render_state() -> None:
    """Clear per-run registries. Call once at the top of each script run."""
    st.session_state[_SEQ_KEY] = {}
    st.session_state[_HEADER_KEY] = set()


def unique_key(base: str) -> str:
    """Return `base`, or `base__2`, `base__3`... if already used this run.

    Deterministic: the same page rendering the same widgets in the same order
    produces the same keys on every rerun, so widget state is preserved.
    """
    seq: dict = st.session_state.setdefault(_SEQ_KEY, {})
    n = seq.get(base, 0) + 1
    seq[base] = n
    return base if n == 1 else f"{base}__{n}"


def claim_once(name: str) -> bool:
    """True the first time `name` is claimed in this script run, False after.

    Used to stop a page header rendering twice.
    """
    seen: set = st.session_state.setdefault(_HEADER_KEY, set())
    if name in seen:
        return False
    seen.add(name)
    return True


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def keyed_container(key: str):
    """A container with a stable element identity, or a plain one if unsupported.

    Streamlit diffs the element tree positionally. When the number of elements
    early in a script run changes between runs -- an st.error that appears on
    one run and not the next, a login form that renders once then goes away --
    the diff can slip and Streamlit appends an element instead of replacing the
    previous one, so the same block shows up twice. Giving a block its own
    keyed container pins its identity so it is always replaced, never appended.

    `key=` on st.container needs Streamlit >= 1.44; older versions just get a
    normal container and lose the protection.
    """
    try:
        return st.container(key=key)
    except TypeError:
        return st.container()


def html_block(content: str) -> None:
    """Render raw HTML. Immune to indentation, unlike st.markdown."""
    cleaned = textwrap.dedent(content).strip()
    try:
        st.html(cleaned)
    except AttributeError:          # Streamlit < 1.33
        st.markdown(cleaned, unsafe_allow_html=True)


def spacer(size: str = "md") -> None:
    """Vertical spacing. Replaces the repeated inline spacer-div markdown."""
    html_block(f'<div class="spacer-{size}"></div>')
