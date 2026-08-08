"""
reporting/auth/session.py

Login gate and session lifecycle.

The gate lives in app.py, *before* `pg.run()`. Because every page is now
reached through `st.navigation` (the `reporting/pages/` directory was renamed
so Streamlit's legacy auto-discovery can no longer serve pages directly),
there is no URL that reaches page code without passing through here.

Threat model, stated plainly: this is a shared-secret gate for trusted
colleagues on your LAN during development. It is not hardened against a
motivated attacker with network access. In particular, **serve this over
HTTPS or the passwords cross the network in clear text** -- see
.streamlit/config.toml for the sslCertFile settings.
"""
from __future__ import annotations

import time
from typing import Any

import streamlit as st

from reporting.auth.passwords import verify_password
from reporting.auth.rls import describe_scope
from reporting.auth.users import (
    AuthConfigError,
    Principal,
    build_principal,
    get_pepper,
    load_user_records,
    lockout_minutes,
    max_failed_attempts,
    session_timeout_minutes,
)

_SESSION_KEY = "_auth_principal"
_LOGIN_AT_KEY = "_auth_login_at"


# ---------------------------------------------------------------------------
# Failed-attempt tracking
# ---------------------------------------------------------------------------
# Kept in a cache_resource dict so it is shared across sessions -- otherwise an
# attacker just opens a new browser tab to reset their own counter.

@st.cache_resource
def _attempt_log() -> dict[str, list[float]]:
    return {}


def _is_locked(email: str) -> float:
    """Return remaining lockout seconds for `email` (0.0 if not locked)."""
    window = lockout_minutes() * 60
    now = time.time()
    attempts = [t for t in _attempt_log().get(email, []) if now - t < window]
    _attempt_log()[email] = attempts
    if len(attempts) >= max_failed_attempts():
        return window - (now - attempts[0])
    return 0.0


def _record_failure(email: str) -> None:
    _attempt_log().setdefault(email, []).append(time.time())


def _clear_failures(email: str) -> None:
    _attempt_log().pop(email, None)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def authenticate(email: str, password: str) -> Principal | None:
    """Return a Principal on success, None on failure."""
    email = (email or "").strip().lower()
    records = load_user_records()
    record: Any = records.get(email)

    if record is None:
        # Still burn the CPU on a dummy verify so response time doesn't reveal
        # whether the address exists.
        verify_password(password, "scrypt$32768$8$1$AAAAAAAAAAAAAAAAAAAAAA==$" + "A" * 44)
        return None

    if not verify_password(password, str(record.get("password", "")), pepper=get_pepper()):
        return None

    return build_principal(email, record)


def current_user() -> Principal | None:
    p = st.session_state.get(_SESSION_KEY)
    if p is None:
        return None
    age_minutes = (time.time() - st.session_state.get(_LOGIN_AT_KEY, 0)) / 60
    if age_minutes > session_timeout_minutes():
        logout()
        return None
    return p


def logout() -> None:
    st.session_state.pop(_SESSION_KEY, None)
    st.session_state.pop(_LOGIN_AT_KEY, None)
    # Filter widget state is keyed per-page; clearing it prevents the next user
    # on the same browser from seeing the previous user's selections.
    for k in [k for k in st.session_state if str(k).startswith("filter_")]:
        st.session_state.pop(k, None)
    st.cache_data.clear()


def login_gate() -> Principal:
    """Render the login form and halt the script until authenticated.

    Returns the authenticated Principal. Calls `st.stop()` otherwise, so the
    caller can treat the return value as always-valid.
    """
    user = current_user()
    if user is not None:
        return user

    _render_login_form()
    st.stop()
    raise AssertionError("unreachable")  # for type checkers


def _render_login_form() -> None:
    st.markdown('<div class="login-wrap">', unsafe_allow_html=True)
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.markdown(
            '<div style="text-align:center; padding:1rem 0 0.5rem 0;">'
            '<div style="font-size:2.2rem;">📊</div>'
            '<div style="color:#F59E0B; font-weight:700; letter-spacing:0.06em;">'
            'SALES ANALYTICS</div>'
            '<div style="color:#6B7280; font-size:0.78rem; margin-top:0.25rem;">'
            'Sign in to continue</div></div>',
            unsafe_allow_html=True,
        )

        with st.form("login_form", clear_on_submit=False):
            email = st.text_input("Email", autocomplete="username")
            password = st.text_input("Password", type="password", autocomplete="current-password")
            submitted = st.form_submit_button("Sign in", width="stretch")

        if submitted:
            key = (email or "").strip().lower()
            locked_for = _is_locked(key)
            if locked_for > 0:
                st.error(f"Too many failed attempts. Try again in {int(locked_for // 60) + 1} min.")
            else:
                try:
                    principal = authenticate(email, password)
                except AuthConfigError as exc:
                    st.error(str(exc))
                    return
                if principal is None:
                    _record_failure(key)
                    # One generic message: never confirm whether the address exists.
                    st.error("Email or password is incorrect.")
                else:
                    _clear_failures(key)
                    st.session_state[_SESSION_KEY] = principal
                    st.session_state[_LOGIN_AT_KEY] = time.time()
                    st.rerun()

        st.markdown(
            '<p style="color:#374151; font-size:0.68rem; text-align:center; margin-top:1rem;">'
            'Internal dashboard · access is logged</p>',
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Sidebar badge
# ---------------------------------------------------------------------------

def render_user_badge(principal: Principal) -> None:
    """Show who is signed in, what they can see, and a sign-out button."""
    with st.sidebar:
        st.markdown(
            f'<div style="background:#111827; border:1px solid #1F2937; border-radius:8px; '
            f'padding:0.6rem 0.75rem; margin:0.5rem 0;">'
            f'<div style="color:#F9FAFB; font-size:0.85rem; font-weight:600;">'
            f'{principal.name}</div>'
            f'<div style="color:#6B7280; font-size:0.7rem;">{principal.role}</div>'
            f'<div style="color:#9CA3AF; font-size:0.7rem; margin-top:0.25rem;">'
            f'{describe_scope(principal)}</div>'
            f"</div>",
            unsafe_allow_html=True,
        )
        if st.button("Sign out", width="stretch", key="_logout_btn"):
            logout()
            st.rerun()


def require(condition: bool, message: str) -> None:
    """Guard a page or feature behind a permission flag."""
    if not condition:
        st.warning(message)
        st.stop()