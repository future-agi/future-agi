"""Identifiers and timestamps used by the fixtures: ULIDs, UUIDv7, RFC 3339 with milliseconds."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid(ms: int, seed: str) -> str:
    """A valid ULID: 48-bit millisecond time + 80 bits taken from sha256(seed). Deterministic for fixtures."""
    rand = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:10], "big")
    value = (ms << 80) | rand
    return "".join(CROCKFORD[(value >> (5 * i)) & 31] for i in range(25, -1, -1))


def uuid7(ms: int, seed: str) -> str:
    """A valid UUIDv7 with the given millisecond time and bits from sha256(seed)."""
    rand = hashlib.sha256(seed.encode()).digest()
    rand_a = int.from_bytes(rand[:2], "big") & 0x0FFF
    rand_b = int.from_bytes(rand[2:10], "big") & ((1 << 62) - 1)
    value = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    h = f"{value:032x}"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def iso_ms(ms: int) -> str:
    return (
        datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{ms % 1000:03d}Z"
    )


def parse_iso_ms(ts: str) -> int:
    dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    return (dt - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(milliseconds=1)
