"""The one version pattern (SPEC §2.3): strict SemVer core, `MAJOR.MINOR.PATCH` with no leading zeros.

Each part is at most 6 digits, so `semver_key`'s zero padding stays one-to-one: `1.0.03` and
`01.0.9` are refused because they would share a sort key with `1.0.3` and `1.0.9`. Used by
services/refs.py (intake, publish, references), the `Version` check constraint, and mirrored by the
checker's U1 (services/omega-hub-checker/src/registry-rules.mjs). No imports, so models can use it.
"""

PART = r"(0|[1-9][0-9]{0,5})"
PATTERN = rf"{PART}\.{PART}\.{PART}"
# For the database: PostgreSQL's `~` reads `$` as the end of the string (no newline exception).
SQL_REGEX = rf"^{PATTERN}$"
