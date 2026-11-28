"""Redis keys and reuse-age constants shared with fi-collector's seen store.

The collector (fi-collector/pkg/trajectory/seen.go) decides whether a span may
point at an item that is already stored instead of writing it again. Lifecycle
code in this repo depends on three parts of that contract:

- How long a stored copy may be reused (``MAX_REUSE_AGE_CAP``). Reference-aware
  GC keeps every copy persisted within this window of any span that is still
  present, plus ``IN_FLIGHT_MARGIN`` for requests that were in flight.
- Where the seen sets live, so erasure can remove one project's keys and a
  restore can clear all of them.
- ``traj:epoch``: a token every collector reads. Changing it makes every pod
  forget what it has seen, in Redis and in its local LRU.
- ``traj:contract:v2``: written by collectors that enforce the reuse bound. GC
  deletes nothing while it is absent.

The bounded seen store (key layout v2) is built in a separate change. Until it
lands the collector still writes the legacy layout ``traj:seen:<pid>:<scope>``,
writes no epoch and no contract key, so GC never deletes (see
``trajectory_lifecycle``). Keep this module and the Go constants in step; the
test ``test_reuse_cap_matches_collector_constant`` compares them once the Go
constant exists.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import timedelta

# Largest reuse age the collector accepts (Go: trajectory.SeenMaxReuseAge).
MAX_REUSE_AGE_CAP = timedelta(hours=48)
# Allowance for a request that read the seen store and wrote its span later.
IN_FLIGHT_MARGIN = timedelta(hours=24)
# GC keeps copies persisted within this margin of the oldest span still present.
GC_MARGIN = MAX_REUSE_AGE_CAP + IN_FLIGHT_MARGIN
# Longest TTL the legacy seen store set on a key (Go: DefaultProjectTTL).
LEGACY_SEEN_MAX_TTL = timedelta(days=7)

EPOCH_KEY = "traj:epoch"
CONTRACT_KEY = "traj:contract:v2"
SEEN_KEY_PREFIX = "traj:seen:"
SEEN_V2_KEY_PREFIX = "traj:seen:v2:"
ALL_SEEN_KEYS_PATTERN = SEEN_KEY_PREFIX + "*"

SCAN_COUNT = 1000
UNLINK_BATCH = 500


def canonical_project_id(project_id: str | uuid.UUID) -> str:
    """The lowercase hyphenated form the collector puts in its keys.

    Raises ValueError for anything that is not a UUID, so a value can never
    widen a SCAN pattern.
    """
    return str(uuid.UUID(str(project_id)))


def project_seen_key_patterns(project_id: str | uuid.UUID) -> tuple[str, ...]:
    """SCAN patterns that match every seen-set key of one project.

    - ``traj:seen:v2:<pid>:*``: bounded layout, project first.
    - ``traj:seen:v2:{<pid>}:*``: the same layout if it is written with a Redis
      Cluster hash tag.
    - ``traj:seen:<pid>:*``: legacy layout (seen.go redisSeenKey).

    None of these matches another project's keys: a UUID contains no glob
    characters and each pattern has the separator after the project id.
    """
    pid = canonical_project_id(project_id)
    return (
        f"{SEEN_V2_KEY_PREFIX}{pid}:*",
        f"{SEEN_V2_KEY_PREFIX}{{{pid}}}:*",
        f"{SEEN_KEY_PREFIX}{pid}:*",
    )


def scan_unlink(client, patterns: Iterable[str]) -> int:
    """UNLINK every key matching ``patterns`` with SCAN, never KEYS.

    Returns the number of keys removed. Keys are removed in batches so one call
    never blocks Redis for long.
    """
    removed = 0
    for pattern in patterns:
        batch: list = []
        for key in client.scan_iter(match=pattern, count=SCAN_COUNT):
            batch.append(key)
            if len(batch) >= UNLINK_BATCH:
                removed += int(client.unlink(*batch) or 0)
                batch = []
        if batch:
            removed += int(client.unlink(*batch) or 0)
    return removed


def clear_project_seen_keys(client, project_id: str | uuid.UUID) -> int:
    """Remove one project's seen sets (both layouts). Returns keys removed."""
    return scan_unlink(client, project_seen_key_patterns(project_id))


def reset_seen_store(client) -> tuple[str, int]:
    """Start a new seen-store epoch, then remove every seen set.

    The epoch is set first: a collector that reads it stops reusing anything it
    saw before, so a key written between the two steps is never trusted. Returns
    the new epoch token and the number of keys removed.
    """
    epoch = uuid.uuid4().hex
    client.set(EPOCH_KEY, epoch)
    return epoch, scan_unlink(client, (ALL_SEEN_KEYS_PATTERN,))


def contract_key_present(client) -> bool:
    """Whether a collector that enforces the reuse bound wrote the contract key.

    Any Redis error counts as absent, which keeps GC from deleting.
    """
    if client is None:
        return False
    from redis.exceptions import RedisError

    try:
        return bool(client.exists(CONTRACT_KEY))
    except (RedisError, OSError):
        return False


def open_seen_redis():
    """A client on the Redis the collector uses (REDIS_URL, database 0)."""
    import redis
    from django.conf import settings

    return redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=30,
    )


__all__ = [
    "ALL_SEEN_KEYS_PATTERN",
    "CONTRACT_KEY",
    "EPOCH_KEY",
    "GC_MARGIN",
    "IN_FLIGHT_MARGIN",
    "LEGACY_SEEN_MAX_TTL",
    "MAX_REUSE_AGE_CAP",
    "canonical_project_id",
    "clear_project_seen_keys",
    "contract_key_present",
    "open_seen_redis",
    "project_seen_key_patterns",
    "reset_seen_store",
    "scan_unlink",
]
