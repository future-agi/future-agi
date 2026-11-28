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
  deletes nothing while it is absent. Any one collector refreshes it (24 h
  TTL), so it does not prove that every collector enforces the bound: a pod
  still on the legacy layout keeps writing ``traj:seen:<pid>:<scope>`` keys
  (TTL up to ``LEGACY_SEEN_MAX_TTL``), and GC also deletes nothing while any
  such key exists.
- ``traj:gc:latch``: set by GC when the reference audit finds a violation.
  GC deletes nothing while it exists; only an operator clears it
  (``trajectory_audit --clear-gc-latch``). It is outside ``traj:seen:*``, so a
  seen-store reset or a project erasure never removes it.
- ``traj:gc:quiet_since``: the ClickHouse server time at which GC first saw the
  contract key and no legacy key, in an unbroken run of daily checks (each
  run renews its ``QUIET_MARK_TTL``, so a pause in checking expires it). A
  legacy pod also reuses from its pod-local LRU for up to
  ``LEGACY_SEEN_MAX_TTL`` after its last Redis read or commit, with no Redis
  key left (expired, evicted, or removed by a reset). GC therefore deletes
  only once this mark is ``LEGACY_QUIET_PERIOD`` old. GC removes the mark when
  it sees a legacy key or no contract key, and a seen-store reset removes it.

The bounded seen store (key layout v2) is built in a separate change. Until it
lands the collector still writes the legacy layout ``traj:seen:<pid>:<scope>``,
writes no epoch and no contract key, so GC never deletes (see
``trajectory_lifecycle``). Keep this module and the Go constants in step; the
test ``test_reuse_cap_matches_collector_constant`` compares them once the Go
constant exists.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from datetime import timedelta

# Largest reuse age the collector accepts (Go: trajectory.SeenMaxReuseAge).
MAX_REUSE_AGE_CAP = timedelta(hours=48)
# Allowance for a request that read the seen store and wrote its span later.
IN_FLIGHT_MARGIN = timedelta(hours=24)
# GC keeps copies persisted within this margin of the oldest span still present.
GC_MARGIN = MAX_REUSE_AGE_CAP + IN_FLIGHT_MARGIN
# Longest TTL the legacy seen store set on a key or a pod-local LRU entry
# (Go: DefaultProjectTTL; no deploy overrides project_ttl).
LEGACY_SEEN_MAX_TTL = timedelta(days=7)
# GC deletes only after it has seen no legacy key, and the contract key, for
# this long: every legacy pod-local LRU entry has expired by then, and a
# request that read one has written its span.
LEGACY_QUIET_PERIOD = LEGACY_SEEN_MAX_TTL + IN_FLIGHT_MARGIN
# Each GC run renews the quiet-period mark for this long, so the period
# starts over when GC stops checking (switch off, schedule paused) for longer.
QUIET_MARK_TTL = timedelta(days=3)

EPOCH_KEY = "traj:epoch"
CONTRACT_KEY = "traj:contract:v2"
GC_LATCH_KEY = "traj:gc:latch"
QUIET_SINCE_KEY = "traj:gc:quiet_since"
SEEN_KEY_PREFIX = "traj:seen:"
SEEN_V2_KEY_PREFIX = "traj:seen:v2:"
ALL_SEEN_KEYS_PATTERN = SEEN_KEY_PREFIX + "*"
# Legacy layout traj:seen:<project uuid>:<scope>. The first eight characters
# of a UUID are hex followed by "-", which no v2 key ("traj:seen:v2:...") has.
LEGACY_SEEN_KEYS_PATTERN = SEEN_KEY_PREFIX + "[0-9a-f]" * 8 + "-*"

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
    # Removing legacy keys does not stop a legacy pod reusing from its local
    # LRU, so GC's quiet period starts again.
    client.delete(QUIET_SINCE_KEY)
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


def gc_latch_present(client) -> bool:
    """Whether ``traj:gc:latch`` is set. Any Redis error counts as set."""
    if client is None:
        return True
    from redis.exceptions import RedisError

    try:
        return bool(client.exists(GC_LATCH_KEY))
    except (RedisError, OSError):
        return True


def set_gc_latch(client, *, reason: str, at_us: int, counts: dict[str, int]) -> bool:
    """Set ``traj:gc:latch`` with no TTL. Returns False if Redis refused it.

    The value holds the reason, the server time and audit counts only.
    """
    from redis.exceptions import RedisError

    body = json.dumps({"reason": reason, "at_us": int(at_us), **counts}, sort_keys=True)
    try:
        client.set(GC_LATCH_KEY, body)
    except (RedisError, OSError):
        return False
    return True


def clear_gc_latch(client) -> bool:
    """Remove ``traj:gc:latch``. Returns whether it was set."""
    return bool(client.delete(GC_LATCH_KEY))


def legacy_seen_keys_present(client) -> bool:
    """Whether any collector wrote a legacy ``traj:seen:<pid>:<scope>`` key.

    A legacy collector may reuse an item for as long as its key lives (up to
    ``LEGACY_SEEN_MAX_TTL``), past the reuse bound GC relies on. SCAN stops at
    the first match. Any Redis error counts as present, which keeps GC from
    deleting.
    """
    if client is None:
        return True
    from redis.exceptions import RedisError

    try:
        keys = client.scan_iter(match=LEGACY_SEEN_KEYS_PATTERN, count=SCAN_COUNT)
        return next(iter(keys), None) is not None
    except (RedisError, OSError):
        return True


def start_quiet_period(client, *, now_us: int) -> int | None:
    """Mark the start of GC's quiet period unless it is marked already.

    Returns the marked start (server microseconds), or None when Redis fails
    or holds a value that is not a time, which keeps GC from deleting.
    """
    if client is None:
        return None
    from redis.exceptions import RedisError

    try:
        client.set(QUIET_SINCE_KEY, str(int(now_us)), nx=True)
        client.expire(QUIET_SINCE_KEY, int(QUIET_MARK_TTL.total_seconds()))
        value = client.get(QUIET_SINCE_KEY)
    except (RedisError, OSError):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def end_quiet_period(client) -> bool:
    """Remove the quiet-period mark. Returns False if Redis refused."""
    if client is None:
        return False
    from redis.exceptions import RedisError

    try:
        client.delete(QUIET_SINCE_KEY)
    except (RedisError, OSError):
        return False
    return True


def open_seen_redis():
    """A client on the Redis the collector uses.

    The host comes from ``REDIS_URL``. The database is always
    ``FI_TRAJECTORY_SEEN_REDIS_DB`` (0, the collector's ``FI_AUTH_REDIS_ADDR``
    database), whatever database ``REDIS_URL`` names: the contract check,
    erasure and the post-restore reset must act on the collector's keyspace.
    """
    import redis
    from django.conf import settings

    client = redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=30,
    )
    # from_url lets the URL's database win over a db= argument, so set it on
    # the pool before any connection is made.
    client.connection_pool.connection_kwargs["db"] = int(
        getattr(settings, "FI_TRAJECTORY_SEEN_REDIS_DB", 0)
    )
    return client


__all__ = [
    "ALL_SEEN_KEYS_PATTERN",
    "CONTRACT_KEY",
    "EPOCH_KEY",
    "GC_LATCH_KEY",
    "GC_MARGIN",
    "IN_FLIGHT_MARGIN",
    "LEGACY_QUIET_PERIOD",
    "LEGACY_SEEN_KEYS_PATTERN",
    "LEGACY_SEEN_MAX_TTL",
    "MAX_REUSE_AGE_CAP",
    "QUIET_MARK_TTL",
    "QUIET_SINCE_KEY",
    "canonical_project_id",
    "clear_gc_latch",
    "clear_project_seen_keys",
    "contract_key_present",
    "end_quiet_period",
    "gc_latch_present",
    "legacy_seen_keys_present",
    "open_seen_redis",
    "project_seen_key_patterns",
    "reset_seen_store",
    "scan_unlink",
    "set_gc_latch",
    "start_quiet_period",
]
