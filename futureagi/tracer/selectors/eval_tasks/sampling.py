"""The sampling hash and the space it maps into.

Both halves of sampling depend on these: the resolver compares a row's hash to
the task's stored threshold, and threshold derivation ranks the same hash to
find that threshold. They live here so the expression has exactly one spelling
— a resolver and a derivation that hashed differently would disagree about
which rows are in the sample.
"""

from __future__ import annotations

# cityHash64 >> 1 — the 63-bit space that fits a signed BIGINT.
HASH_SPACE = 2**63


def sampling_hash_sql(salt_param: str, id_col: str) -> str:
    """The row's sampling hash, as a ClickHouse expression."""
    return f"bitShiftRight(cityHash64(%({salt_param})s, toString({id_col})), 1)"
