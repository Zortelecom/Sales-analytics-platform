"""
reporting/auth/users.py

The user directory and the `Principal` object that carries a logged-in user's
identity *and* their row-level scope through the request.

Users are declared in `.streamlit/secrets.toml` (never committed). See
`.streamlit/secrets.example.toml` for the shape.

A `Principal` with a scope tuple set to `None` means "unrestricted on that
dimension". An empty tuple `()` means "restricted to nothing" -- i.e. sees no
rows. That distinction is deliberate: `None` and `()` must never be conflated,
or a misconfigured user silently gets full access.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import streamlit as st

# Dimensions a user can be scoped on. Keep in sync with reporting/auth/rls.py.
SCOPE_DIMENSIONS = ("regions", "subregions", "salespersons", "supervisors", "channels", "clients")


class AuthConfigError(RuntimeError):
    """Raised when the [auth] block in secrets.toml is missing or malformed."""


@dataclass(frozen=True)
class Principal:
    email: str
    name: str
    role: str
    # None => unrestricted on that dimension. () => sees nothing.
    regions: tuple[str, ...] | None = None
    subregions: tuple[str, ...] | None = None
    salespersons: tuple[str, ...] | None = None
    supervisors: tuple[str, ...] | None = None
    channels: tuple[str, ...] | None = None
    clients: tuple[str, ...] | None = None
    can_ask_data: bool = False
    can_export: bool = True
    pages: tuple[str, ...] | None = None      # None => every page
    _key: str = field(default="", repr=False, compare=False)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_scoped(self) -> bool:
        """True if at least one RLS dimension is restricted."""
        return any(getattr(self, d) is not None for d in SCOPE_DIMENSIONS)

    def scope_items(self) -> list[tuple[str, tuple[str, ...]]]:
        """Yield (dimension, allowed_values) for every restricted dimension."""
        out = []
        for d in SCOPE_DIMENSIONS:
            v = getattr(self, d)
            if v is not None:
                out.append((d, v))
        return out

    def cache_key(self) -> str:
        """Stable identity string used to partition the query cache.

        Two users with identical scope legitimately share cached results, so
        the key is derived from the *scope*, not the email. That keeps the
        cache useful while making cross-user leakage structurally impossible.
        """
        if self._key:
            return self._key
        payload = json.dumps(
            {d: getattr(self, d) for d in SCOPE_DIMENSIONS} | {"admin": self.is_admin},
            sort_keys=True,
            default=list,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def allows_page(self, page_key: str) -> bool:
        return self.pages is None or page_key in self.pages


# Role presets. A user's explicit keys always win over the preset.
#
# The audiences for this report are: the report owner / data analyst, Regional
# Business Managers, and supervisors. Salespeople are NOT report consumers --
# `salespersons` remains available as a scope dimension (it is how fact_targets
# and v_weekly_kpi get filtered indirectly) but is not expected to be a role.
ROLE_DEFAULTS: dict[str, dict[str, Any]] = {
    # Report owner / data analyst. Unrestricted; scope keys are rejected.
    "admin":      {"can_ask_data": True,  "can_export": True},
    # Same visibility as admin but not privileged in the app -- use for a
    # second analyst you don't want editing anything.
    "analyst":    {"can_ask_data": True,  "can_export": True},
    # Regional Business Manager: all regions by default. Add `regions = [...]`
    # only if you later split the role per region.
    "rbm":        {"can_ask_data": False, "can_export": True},
    # Supervisor: scope with `subregions`. See rls.py for why subregion beats
    # supervisor_name as the primary boundary.
    "supervisor": {"can_ask_data": False, "can_export": True},
    # Most restrictive fallback for anyone whose role key is unrecognised.
    "viewer":     {"can_ask_data": False, "can_export": False},
}


def _as_tuple(value: Any) -> tuple[str, ...] | None:
    """Normalise a secrets value into a scope tuple.

    Missing / null  -> None  (unrestricted)
    "Littoral"      -> ("Littoral",)
    ["A", "B"]      -> ("A", "B")
    []              -> ()      (sees nothing -- kept, not upgraded to None)
    """
    if value is None:
        return None
    if isinstance(value, str):
        return (value.strip(),)
    return tuple(str(v).strip() for v in value)


# ---------------------------------------------------------------------------
# Loading [auth] from secrets.toml
# ---------------------------------------------------------------------------
# `st.secrets` resolves .streamlit/secrets.toml relative to the process working
# directory, not to the app file. Launch from anywhere other than the project
# root -- or from an IDE that sets its own cwd -- and Streamlit silently finds
# nothing, which surfaced as a confusing "No [auth] section found".
#
# So: try st.secrets first (it hot-reloads on edit, which is convenient), and
# fall back to reading the file ourselves from a path anchored on this module.
# reporting/auth/users.py -> parents[2] is the project root regardless of cwd.

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _candidate_paths() -> list[Path]:
    """Where a secrets.toml may live, in priority order."""
    paths = []
    override = os.environ.get("REPORTING_SECRETS_PATH")
    if override:
        paths.append(Path(override))
    paths.append(_PROJECT_ROOT / ".streamlit" / "secrets.toml")
    # Streamlit itself never looks here, but people put it next to app.py:
    paths.append(_PROJECT_ROOT / "reporting" / ".streamlit" / "secrets.toml")
    paths.append(Path.cwd() / ".streamlit" / "secrets.toml")
    paths.append(Path.home() / ".streamlit" / "secrets.toml")
    # de-duplicate while preserving order
    seen, out = set(), []
    for p in paths:
        rp = str(p.resolve()) if p.exists() else str(p)
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    return out


def _load_toml(path: Path) -> dict:
    data = path.read_bytes()
    try:
        import tomllib                       # Python 3.11+
        return tomllib.loads(data.decode("utf-8"))
    except ImportError:
        import toml                          # ships with Streamlit
        return toml.loads(data.decode("utf-8"))


def _auth_config() -> Mapping[str, Any]:
    # 1. Streamlit's own resolution, when it works.
    try:
        return st.secrets["auth"]
    except Exception:
        pass

    # 2. Read the file directly.
    tried = _candidate_paths()
    for path in tried:
        if not path.is_file():
            continue
        try:
            parsed = _load_toml(path)
        except Exception as exc:
            raise AuthConfigError(
                f"{path} exists but could not be parsed as TOML: {exc}"
            ) from exc
        if "auth" in parsed:
            return parsed["auth"]
        raise AuthConfigError(
            f"{path} was found and parsed, but has no [auth] section. "
            f"Top-level keys present: {sorted(parsed) or 'none'}. "
            f"Check you copied secrets.example.toml and not config.toml."
        )

    listing = "\n".join(f"  - {p}   ({'exists' if p.exists() else 'not found'})"
                        for p in tried)
    raise AuthConfigError(
        "No secrets.toml with an [auth] section was found. Searched:\n"
        f"{listing}\n"
        f"Working directory is {Path.cwd()}. Copy "
        ".streamlit/secrets.example.toml to .streamlit/secrets.toml at the "
        "project root, or set REPORTING_SECRETS_PATH to its full path."
    )


def get_pepper() -> str:
    return str(_auth_config().get("pepper", ""))


def session_timeout_minutes() -> int:
    return int(_auth_config().get("session_timeout_minutes", 480))


def max_failed_attempts() -> int:
    return int(_auth_config().get("max_failed_attempts", 5))


def lockout_minutes() -> int:
    return int(_auth_config().get("lockout_minutes", 10))


def load_user_records() -> dict[str, Mapping[str, Any]]:
    """Return {lowercased_email: record} from secrets.toml."""
    users = _auth_config().get("users")
    if not users:
        raise AuthConfigError("[auth.users] is empty -- nobody can log in.")
    return {str(email).strip().lower(): rec for email, rec in users.items()}


def build_principal(email: str, record: Mapping[str, Any]) -> Principal:
    """Turn a secrets record into a Principal, applying role defaults."""
    role = str(record.get("role", "viewer")).strip().lower()
    defaults = ROLE_DEFAULTS.get(role, ROLE_DEFAULTS["viewer"])

    scopes: dict[str, tuple[str, ...] | None] = {}
    for dim in SCOPE_DIMENSIONS:
        scopes[dim] = _as_tuple(record.get(dim))

    # An admin is unrestricted by definition. Fail loudly rather than silently
    # ignoring a scope that a well-meaning edit added to an admin record.
    if role == "admin" and any(v is not None for v in scopes.values()):
        raise AuthConfigError(
            f"User {email} has role='admin' but also has scope keys set. "
            "Give them a non-admin role, or drop the scope keys."
        )

    return Principal(
        email=email,
        name=str(record.get("name", email.split("@")[0])),
        role=role,
        can_ask_data=bool(record.get("can_ask_data", defaults["can_ask_data"])),
        can_export=bool(record.get("can_export", defaults["can_export"])),
        pages=_as_tuple(record.get("pages")),
        **scopes,
    )