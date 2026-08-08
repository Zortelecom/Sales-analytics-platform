"""Authentication and row-level security for the reporting app."""
from reporting.auth.session import (  # noqa: F401
    authenticate,
    current_user,
    login_gate,
    logout,
    render_user_badge,
    require,
)
from reporting.auth.users import AuthConfigError, Principal  # noqa: F401

__all__ = [
    "authenticate", "current_user", "login_gate", "logout",
    "render_user_badge", "require", "Principal", "AuthConfigError",
]