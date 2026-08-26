"""
reporting/utils/markup.py

Render hand-built HTML through Streamlit without it being eaten by Markdown.

THE BUG THIS EXISTS TO KILL
---------------------------
`st.markdown(html, unsafe_allow_html=True)` does not render HTML directly. It
renders MARKDOWN that is *allowed to contain* HTML, and CommonMark has two
rules that quietly destroy a hand-built table:

  1. A line indented four or more spaces is an INDENTED CODE BLOCK. Every
     f-string built inside a `with` block and a `for` loop is indented far
     past four spaces, so the browser is shown the tags as literal text --
     exactly the `<tr style="border-bottom:1px solid #1F2937;">` visible in the
     Product Detail Table and the Weekly Breakdown panel.

  2. A BLANK LINE closes an HTML block. Rows accumulated with
     `rows += f\"\"\"\\n<tr>...\\n\"\"\"` are separated by blank lines, so only the
     first row stays inside the HTML block and everything after it is
     re-parsed as new Markdown.

Both are properties of the *text*, not of the styling, which is why the tables
render correctly in some panels and not others depending purely on how deeply
the f-string happened to be nested.

THE FIX
-------
Collapse the markup to a SINGLE LINE before handing it over: strip every line,
drop the empty ones, join with a space. No line can then be indented and no
blank line can appear, so neither rule can fire regardless of how the caller
indented the source.

Joined with a space rather than nothing: HTML collapses runs of whitespace, so
a space between `</td>` and `<td>` is invisible, whereas joining with nothing
would weld together any text value that the author happened to wrap across two
source lines.

⚠ Do not use this for content containing <pre> or <textarea>, where newlines
and indentation are significant.

⚠ Values interpolated into the markup are NOT escaped -- this renders with
unsafe_allow_html=True by definition. Everything passed here today is either a
literal, a formatted number, or a product/region name out of the warehouse. If
a value ever originates from user input, escape it with `html.escape()` first.
"""
from __future__ import annotations

import streamlit as st


def clean_html(markup: str) -> str:
    """Collapse multi-line markup to one line Markdown cannot misread."""
    return " ".join(
        line.strip() for line in markup.splitlines() if line.strip()
    )


def html_block(markup: str) -> None:
    """Render hand-built HTML. Use INSTEAD OF st.markdown(..., unsafe_allow_html=True)."""
    st.markdown(clean_html(markup), unsafe_allow_html=True)


def html_table(headers_html: str, rows_html: str,
               colors: dict,
               max_height: str | None = None,
               font_size: str = "0.83rem") -> None:
    """A styled table, so the wrapper markup lives in one place.

    `headers_html` is the <th>...</th> run, `rows_html` the <tr>...</tr> run.
    Both are cleaned along with the wrapper, so callers may indent freely.
    """
    scroll_open = (
        f'<div style="overflow-y:auto; max-height:{max_height};">'
        if max_height else ""
    )
    scroll_close = "</div>" if max_height else ""
    html_block(f"""
        {scroll_open}
        <table style="width:100%; border-collapse:collapse; font-size:{font_size};">
            <thead>
                <tr style="background:{colors['bg_card_alt']}; color:{colors['text_secondary']};
                           font-size:0.72rem; text-transform:uppercase;">
                    {headers_html}
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
        {scroll_close}
    """)
