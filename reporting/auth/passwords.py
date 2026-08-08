"""
reporting/auth/passwords.py

Password hashing using stdlib `hashlib.scrypt` -- no extra dependency
(bcrypt / argon2 / passlib are all fine too, this just avoids adding one).

Encoded format:  scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>

Generate a hash for a colleague:

    python -m reporting.auth.passwords
    # or non-interactively (leaves the password in shell history -- avoid):
    python -m reporting.auth.passwords --password 'their-password'

Then paste the printed string into .streamlit/secrets.toml.
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import os
import secrets

# scrypt cost parameters. n=2**15 costs ~50-100 ms per verify on a laptop,
# which is a deliberate brute-force speed bump. Raise n if login feels
# instant; lower it (2**14) if the login form feels sluggish on old hardware.
_N = 2 ** 15
_R = 8
_P = 1
_DKLEN = 32
_SALT_BYTES = 16


def hash_password(password: str, *, pepper: str = "") -> str:
    """Return an encoded scrypt hash for `password`.

    `pepper` is a server-side secret mixed into every hash. It lives in
    secrets.toml alongside the hashes, so it is NOT a defence against someone
    who reads that file -- it only helps if hashes leak on their own (e.g.
    pasted into a chat, committed to git). Changing it invalidates every
    existing hash.
    """
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.scrypt(
        (password + pepper).encode("utf-8"),
        salt=salt,
        n=_N, r=_R, p=_P, dklen=_DKLEN,
        maxmem=64 * 1024 * 1024,
    )
    return "scrypt${}${}${}${}${}".format(
        _N, _R, _P,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    )


def verify_password(password: str, encoded: str, *, pepper: str = "") -> bool:
    """Constant-time verification of `password` against an encoded hash."""
    try:
        scheme, n, r, p, salt_b64, hash_b64 = encoded.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.scrypt(
            (password + pepper).encode("utf-8"),
            salt=salt,
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
            maxmem=64 * 1024 * 1024,
        )
    except (ValueError, TypeError, MemoryError):
        return False
    # hmac.compare_digest, not ==, so verification time does not leak how many
    # leading bytes matched.
    return hmac.compare_digest(dk, expected)


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Generate a password hash for secrets.toml")
    ap.add_argument("--password", help="password (omit to be prompted -- preferred)")
    ap.add_argument("--pepper", default="", help="must match [auth].pepper in secrets.toml")
    args = ap.parse_args()

    pw = args.password
    if not pw:
        pw = getpass.getpass("Password: ")
        if pw != getpass.getpass("Confirm : "):
            raise SystemExit("Passwords do not match.")
    if len(pw) < 10:
        print("WARNING: shorter than 10 characters. Consider a passphrase.")

    print()
    print('password = "' + hash_password(pw, pepper=args.pepper) + '"')


if __name__ == "__main__":
    _main()