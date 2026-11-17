"""Reviewed exact-callsite exceptions, not ambient SQL authority.

SAML transaction B uses two shared row locks to fence token minting against user
or membership removal. They change no column values, but are not pure reads.
Keep these product fences without permitting the same SQL from any other caller.
All callsite coordinates and statement bytes must match; no executemany permit.
"""

import sys
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SQL_EXCEPTIONS = (
    {
        "file": "saml2_auth/services.py",
        "function": "issue_token",
        "line": 490,
        "fingerprint": "0fe213acd4c82a7e5cadf57a2bb5d9864595d594e956a62692bb2ac3ef3845be",
        "justification": "Transaction-B user liveness fence: FOR SHARE prevents concurrent deactivation while SAML token minting commits; no credential or identity value mutation.",
    },
    {
        "file": "saml2_auth/services.py",
        "function": "issue_token",
        "line": 498,
        "fingerprint": "2781e3877ebc463a0733232f8d8f4c05c4069a51b588091e6f03831cde8b1bba",
        "justification": "Transaction-B active organization-membership fence: FOR SHARE prevents concurrent offboarding during SAML token minting; no column mutation.",
    },
)
_GUARD_FILES = {
    str(Path(__file__).resolve()),
    str(ROOT / "accounts/services/hard_erase_sql.py"),
}


def exact_sql_exception(sql, many, connection):
    fingerprint = sha256(sql.encode()).hexdigest()
    matches = [row for row in SQL_EXCEPTIONS if row["fingerprint"] == fingerprint]
    if not matches:
        return False
    if many or not connection.in_atomic_block or connection.alias != "default":
        return False
    frame = sys._getframe(1)
    try:
        while frame is not None:
            filename = frame.f_code.co_filename
            module = frame.f_globals.get("__name__", "")
            if filename in _GUARD_FILES or module.startswith("django.db.backends."):
                frame = frame.f_back
                continue
            # Stop at the first application caller. A callback/helper cannot
            # borrow authority from an approved function further up the stack.
            return any(
                filename == str(ROOT / row["file"])
                and frame.f_code.co_qualname == row["function"]
                and frame.f_lineno == row["line"]
                and module == "saml2_auth.services"
                for row in matches
            )
        return False
    finally:
        del frame
