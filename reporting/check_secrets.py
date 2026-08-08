"""
check_secrets.py — diagnose "No [auth] section found".

    python check_secrets.py

Run it from the same directory you run `streamlit run reporting/app.py` from.
It reports which paths were searched, what parsed, and whether each user
record is well-formed. It never prints password hashes.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def load_toml(path: Path) -> dict:
    data = path.read_bytes()
    try:
        import tomllib
        return tomllib.loads(data.decode("utf-8"))
    except ImportError:
        import toml
        return toml.loads(data.decode("utf-8"))


def main() -> int:
    print(f"cwd            : {Path.cwd()}")
    print(f"project root   : {ROOT}")
    print(f"override env   : {os.environ.get('REPORTING_SECRETS_PATH') or '(unset)'}")
    print()

    candidates = []
    if os.environ.get("REPORTING_SECRETS_PATH"):
        candidates.append(Path(os.environ["REPORTING_SECRETS_PATH"]))
    candidates += [
        ROOT / ".streamlit" / "secrets.toml",
        Path.cwd() / ".streamlit" / "secrets.toml",
        Path.home() / ".streamlit" / "secrets.toml",
    ]

    seen: set[str] = set()
    deduped: list[Path] = []
    for p in candidates:
        key = str(p.resolve()) if p.exists() else str(p)
        if key not in seen:
            seen.add(key)
            deduped.append(p)
    candidates = deduped

    found: Path | None = None
    for p in candidates:
        mark = "FOUND  " if p.is_file() else "missing"
        print(f"  [{mark}] {p}")
        if p.is_file() and found is None:
            found = p

    # A very common Windows cause: Notepad / "Save as" appending .txt
    for d in {ROOT / ".streamlit", Path.cwd() / ".streamlit"}:
        if d.is_dir():
            strays = [f.name for f in d.iterdir()
                      if f.name.lower().startswith("secrets")
                      and f.name.lower() != "secrets.toml"]
            if strays:
                print(f"\n  ! {d} also contains: {', '.join(strays)}")
                print("    If one of those is your real file, rename it to exactly "
                      "'secrets.toml'.")
                print("    In Explorer: View > Show > File name extensions, so you "
                      "can see a hidden .txt suffix.")

    if found is None:
        print("\nNo secrets.toml anywhere. Copy the example:")
        print(r"  copy .streamlit\secrets.example.toml .streamlit\secrets.toml")
        return 1

    print(f"\nParsing {found}")
    try:
        parsed = load_toml(found)
    except Exception as exc:
        print(f"  TOML PARSE ERROR: {exc}")
        print("  Common causes: a value not wrapped in quotes, a stray tab, or a")
        print("  smart quote pasted from a document instead of a straight \".")
        return 1

    print(f"  top-level keys: {sorted(parsed) or '(none)'}")
    if "auth" not in parsed:
        print("\n  No [auth] section. Did you copy config.toml by mistake?")
        return 1

    auth = parsed["auth"]
    print(f"  [auth] keys   : {sorted(k for k in auth if k != 'users')}")

    pepper = auth.get("pepper", "")
    if not pepper:
        print("\n  ! [auth].pepper is missing or empty.")
    elif pepper == "replace-this-with-a-long-random-string":
        print("\n  ! [auth].pepper is still the example value. Change it, then")
        print("    regenerate every password hash with the new pepper.")

    users = auth.get("users") or {}
    if not users:
        print("\n  ! [auth.users] is empty. Add at least one:")
        print('      [auth.users."you@company.com"]')
        return 1

    print(f"\n  {len(users)} user record(s):")
    ok = True
    for email, rec in users.items():
        problems = []
        pw = str(rec.get("password", ""))
        if not pw:
            problems.append("no password")
        elif not pw.startswith("scrypt$"):
            problems.append("password is not a scrypt hash "
                            "(run: python -m reporting.auth.passwords)")
        elif "replace" in pw:
            problems.append("password is still the placeholder")
        role = str(rec.get("role", "")).lower()
        if role not in {"admin", "analyst", "rbm", "supervisor", "viewer"}:
            problems.append(f"unknown role {role!r} -> falls back to 'viewer'")
        if role == "admin" and any(
            k in rec for k in ("regions", "subregions", "salespersons",
                               "supervisors", "channels", "clients")
        ):
            problems.append("admin records must not carry scope keys")
        if role == "supervisor" and not rec.get("subregions"):
            problems.append("supervisor with no `subregions` sees everything")

        status = "ok" if not problems else "; ".join(problems)
        print(f"    - {email:<38} role={role or '?':<11} {status}")
        ok = ok and not problems

    print("\n" + ("All good." if ok else "Fix the items above."))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
