"""Prefixed ULIDs (DESIGN.md section 4) and RFC 3339 timestamps with milliseconds (section 14.1)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

PREFIXES = frozenset(
    {"evt", "evt_test", "whep", "whk", "whsub", "dlv", "att", "whpl", "whpol"}
)


def ulid(ms: int, rand: bytes | None = None) -> str:
    """26 Crockford characters: 48 bits of milliseconds, then 80 random bits. Never contains '.'."""
    if not 0 <= ms < 2**48:
        raise ValueError("ulid time out of range")
    r = rand if rand is not None else os.urandom(10)
    if len(r) != 10:
        raise ValueError("ulid needs 10 random bytes")
    value = (ms << 80) | int.from_bytes(r, "big")
    return "".join(CROCKFORD[(value >> (5 * i)) & 31] for i in range(25, -1, -1))


def new_id(prefix: str, ms: int, rand: bytes | None = None) -> str:
    if prefix not in PREFIXES:
        raise ValueError(f"unknown id prefix {prefix!r}")
    return f"{prefix}_{ulid(ms, rand)}"


def ulid_ms(value: str) -> int:
    """The millisecond time inside a ULID or a prefixed id."""
    raw = value.rsplit("_", 1)[-1]
    if len(raw) != 26:
        raise ValueError("not a ULID")
    n = 0
    for ch in raw:
        n = n * 32 + CROCKFORD.index(ch)
    return n >> 80


def iso_ms(ms: int) -> str:
    """``2026-10-22T09:59:58.120Z``: UTC, always three fraction digits."""
    dt = _EPOCH + timedelta(milliseconds=ms)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"


def parse_iso_ms(ts: str) -> int:
    dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    return (dt - _EPOCH) // timedelta(milliseconds=1)


def datetime_ms(dt: datetime) -> int:
    """Milliseconds since the epoch of an aware datetime."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return (dt - _EPOCH) // timedelta(milliseconds=1)
