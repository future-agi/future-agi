"""Atomic cache boundary for exact analytics snapshots.

The cache is deliberately a result cache, not a work queue.  A caller either
publishes one fully-computed exact payload with ``cache.set`` (an atomic Redis
replacement), or leaves the previous payload untouched.  Partial, sampled,
and degraded responses are rejected at this boundary.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

import structlog
from django.conf import settings
from django.core.cache import cache

logger = structlog.get_logger(__name__)

# Bump whenever a release changes exact-query semantics. Cache keys are shared
# across deployments and snapshots live for up to 30 days, so reusing the old
# namespace could otherwise serve results computed by pre-deploy code.
# 5: session-graph and eval primary-traffic buckets are keyed by one UTC form.
_CACHE_VERSION = 5
# Admission is a shared resource guard, not a result contract. Preserve its
# deployed key across semantic cache bumps so rolling workers do not each get
# an independent allowance for the same project's expensive queries.
_SCOPE_ADMISSION_KEY_VERSION = 3
_DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60
EXACT_AGGREGATION_ACTIVITY_TIMEOUT_SECONDS = 60 * 60
EXACT_AGGREGATION_SCHEDULE_TO_START_TIMEOUT_SECONDS = 12 * 60 * 60
EXACT_AGGREGATION_WORKFLOW_RUN_TIMEOUT_SECONDS = 14 * 60 * 60
EXACT_AGGREGATION_WORKFLOW_EXECUTION_TIMEOUT_SECONDS = 24 * 60 * 60
# The running lease must outlive the Temporal start-to-close timeout.  Without
# this margin, a replacement claim could start while the timed-out worker's
# synchronous ClickHouse call is still unwinding.  Token fencing would protect
# publication, but it would not protect ClickHouse from overlapping work.
_DEFAULT_REFRESH_LOCK_SECONDS = EXACT_AGGREGATION_ACTIVITY_TIMEOUT_SECONDS + 5 * 60
# A refresh claim starts as a dispatch lease and is promoted to the shorter
# running lease before it touches ClickHouse.  The dedicated worker deliberately
# admits one activity at a time, so valid work can wait behind another exact read.
# Keep this lease beyond TaskRunnerWorkflow's twelve-hour schedule-to-start
# ceiling; terminal-workflow reconciliation still releases incompatible-worker
# failures immediately when a client polls.
_DEFAULT_REFRESH_DISPATCH_SECONDS = (
    EXACT_AGGREGATION_SCHEDULE_TO_START_TIMEOUT_SECONDS + 60 * 60
)
_DEFAULT_REFRESH_RECONCILE_SECONDS = 5
_DEFAULT_REFRESH_STATUS_TIMEOUT_SECONDS = 0.5
_DEFAULT_REFRESH_FAILURE_SECONDS = 5 * 60
_CACHE_FENCE_FALLBACK_LOCK = RLock()
_ALLOWED_EXACT_AGGREGATION_TASK_QUEUES = frozenset({"tasks_xl", "exact_aggregation"})
_DEFAULT_MAX_INFLIGHT_PER_SCOPE = 2
# Open-window revalidation (``read_or_schedule_exact_snapshot``) applies only to
# the Observe charts that read the hour-stable toolbar window: the three
# system-metric charts and the Agent Graph (whose filters carry the same
# window, so a revisit replays the same identity). Dashboards, eval usage,
# attribute detail and eval/annotation charts keep reload-only snapshots.
_OPEN_WINDOW_REVALIDATION_NAMESPACES = frozenset(
    {
        "observe-system-graph",
        "observe-session-system-graph",
        "observe-user-system-graph",
        "observe-agent-graph",
    }
)
# How old an open-window snapshot must be before a visit refreshes it
# (setting EXACT_AGGREGATION_REVALIDATE_AFTER_SECONDS; 0 or None turns it off).
# Why five minutes, from the two load boundaries a revalidation passes through:
# - Per identity, the refresh lock plus the "no running or failed state" gate
#   allow one refresh in flight, and the next cannot start before
#   ``completed_at + floor``. A continuously viewed identity whose exact read
#   takes D seconds therefore occupies a worker slot at most D / (D + floor) of
#   the time.
# - Per admission scope (the identity's project_id, else workspace_id, else
#   organization_id), a revalidation claims admission only while it leaves a
#   slot free (``_revalidation_admission_limit``): with the default two
#   slots, it claims only when the scope has NO exact job in flight, so at
#   most one revalidation per scope runs at a time and a user's new chart
#   always has the other slot. (With EXACT_AGGREGATION_MAX_INFLIGHT_PER_SCOPE=1
#   there is no slot to spare, and a revalidation may take the only one.)
#   The cost of that reservation: while ANY exact job of the same scope is in
#   flight (another chart's cold read, an eval chart, another revalidation),
#   an old open-window hit is served plain, complete and not refreshing, with
#   nothing retrying until the next visit. How old it can get depends on how
#   long its identity lives, not on the floor:
#   - Rolling presets (7D .. 12M): the start is floored to the UTC hour, so
#     the identity changes each hour and such a hit is at most about one hour
#     old (plus read time).
#   - "Today" ([local midnight, next local midnight], never rounded; the
#     default window in user mode): the identity lives all day, so on a scope
#     that often has an exact job in flight the hit can be many hours old.
#   - A custom window ending in the future: the identity lives until the
#     viewer changes it, so there is no bound at all.
#   (Yesterday and the sub-day presets end by the time they are computed, so
#   they never revalidate.) In every case the only sign is completed_at
#   ("Last updated").
# - A revalidation whose Temporal dispatch is accepted but never starts keeps
#   its "running" state for the dispatch lease, exactly like an explicit
#   refresh today: hit polls never reconcile against Temporal, so the chart
#   shows "Refreshing data" until the poll budget pauses it; an explicit
#   Reload takes the scheduling path, which reconciles a terminal dispatch.
# With a free slot, a revisited open-window chart older than the floor is
# served marked refreshing while its own refresh runs; the failed-state TTL
# (EXACT_AGGREGATION_REFRESH_FAILURE_SECONDS, also 300 s by default, an
# independent setting) is the retry backoff after a failure.
_DEFAULT_REVALIDATE_AFTER_SECONDS = 5 * 60
# Configuration cannot turn every poll into a claim.
_MIN_REVALIDATE_AFTER_SECONDS = 60

_REDIS_ATOMIC_REFRESH_CLAIM_SCRIPT = """
local claimed = redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[3], 'NX')
if not claimed then
    return 0
end
redis.call('SET', KEYS[2], ARGV[2], 'PX', ARGV[3])
return 1
"""

_REDIS_FENCED_ROLLBACK_REFRESH_CLAIM_SCRIPT = """
local removed = 0
if redis.call('GET', KEYS[2]) == ARGV[2] then
    redis.call('DEL', KEYS[2])
    removed = removed + 1
end
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('DEL', KEYS[1])
    removed = removed + 1
end
return removed
"""

_REDIS_FENCED_PUBLISH_SCRIPT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return 0
end
local ttl_ms = tonumber(ARGV[3])
if ttl_ms > 0 then
    redis.call('SET', KEYS[2], ARGV[2], 'PX', ttl_ms)
else
    redis.call('SET', KEYS[2], ARGV[2])
end
redis.call('DEL', KEYS[3])
redis.call('DEL', KEYS[1])
return 1
"""

_REDIS_FENCED_FINISH_SCRIPT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return 0
end
if ARGV[2] == '1' then
    redis.call('DEL', KEYS[2])
else
    redis.call('SET', KEYS[2], ARGV[3], 'PX', ARGV[4])
end
redis.call('DEL', KEYS[1])
return 1
"""

_REDIS_FENCED_ACTIVATE_SCRIPT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return 0
end
redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2])
redis.call('SET', KEYS[2], ARGV[3], 'PX', ARGV[2])
return 1
"""

_REDIS_FENCED_RECORD_DISPATCH_SCRIPT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return 0
end
if redis.call('GET', KEYS[2]) ~= ARGV[2] then
    return 0
end
redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[4])
redis.call('SET', KEYS[2], ARGV[3], 'PX', ARGV[4])
return 1
"""

_REDIS_FENCED_RELEASE_DISPATCH_SCRIPT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return 0
end
if redis.call('GET', KEYS[2]) ~= ARGV[2] then
    return 0
end
redis.call('DEL', KEYS[2])
redis.call('DEL', KEYS[1])
return 1
"""

_REDIS_CLAIM_SCOPE_ADMISSION_SCRIPT = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if redis.call('ZSCORE', KEYS[1], ARGV[2]) then
    redis.call('ZADD', KEYS[1], ARGV[3], ARGV[2])
    local latest = redis.call('ZREVRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    local ttl = tonumber(latest[2]) - tonumber(ARGV[1]) + tonumber(ARGV[4])
    redis.call('PEXPIRE', KEYS[1], math.max(1, ttl))
    return 1
end
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[5]) then
    return 0
end
redis.call('ZADD', KEYS[1], ARGV[3], ARGV[2])
local latest = redis.call('ZREVRANGE', KEYS[1], 0, 0, 'WITHSCORES')
local ttl = tonumber(latest[2]) - tonumber(ARGV[1]) + tonumber(ARGV[4])
redis.call('PEXPIRE', KEYS[1], math.max(1, ttl))
return 1
"""

_REDIS_RENEW_SCOPE_ADMISSION_SCRIPT = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if not redis.call('ZSCORE', KEYS[1], ARGV[2]) then
    return 0
end
redis.call('ZADD', KEYS[1], ARGV[3], ARGV[2])
local latest = redis.call('ZREVRANGE', KEYS[1], 0, 0, 'WITHSCORES')
local ttl = tonumber(latest[2]) - tonumber(ARGV[1]) + tonumber(ARGV[4])
redis.call('PEXPIRE', KEYS[1], math.max(1, ttl))
return 1
"""

_REDIS_RELEASE_SCOPE_ADMISSION_SCRIPT = """
local removed = redis.call('ZREM', KEYS[1], ARGV[1])
if redis.call('ZCARD', KEYS[1]) == 0 then
    redis.call('DEL', KEYS[1])
end
return removed
"""


@dataclass(frozen=True)
class ExactAggregationSnapshot:
    payload: Any
    completed_at: str
    cache_hit: bool


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value
        return normalized.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_json_value(item) for item in value]
        if isinstance(value, (set, frozenset)):
            return sorted(items, key=lambda item: _canonical_json(item))
        return items
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported snapshot identity type: {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def snapshot_cache_key(namespace: str, identity: Any) -> str:
    """Return a tenant-safe fixed-width key for one normalized query."""

    if not namespace:
        raise ValueError("snapshot namespace is required")
    digest = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
    return f"exact-aggregation:v{_CACHE_VERSION}:{namespace}:{digest}"


def normalized_snapshot_identity(identity: Any) -> Any:
    """Return the same JSON-safe identity representation used by cache keys."""

    return _json_value(identity)


def _utc_filter_bound(value: datetime) -> str:
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return normalized.astimezone(UTC).isoformat().replace("+00:00", "Z")


def normalize_exact_observe_identity(identity: Any) -> Any:
    """Freeze and canonicalize one exact Observe query identity.

    HTTP serializers validate the public filter vocabulary. This boundary owns
    the additional invariants needed by asynchronous exact reads: the rolling
    default window becomes an explicit half-open interval exactly once, filter
    conjunction/value ordering cannot multiply equivalent jobs, and UI-only
    filter metadata never enters either the cache key or worker payload.
    """

    normalized_identity = normalized_snapshot_identity(identity)
    if not isinstance(normalized_identity, dict):
        return normalized_identity
    # Only Observe query identities carry the public filter conjunction. Keep
    # low-level lifecycle/test identities byte-for-byte compatible; their
    # callers do not execute a graph query from this dictionary.
    if "filters" not in normalized_identity:
        return normalized_identity

    from tracer.serializers.filters import validate_filter_list_complexity
    from tracer.services.clickhouse.list_cursor import normalize_filter_conjunction
    from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

    filters = normalize_filter_conjunction(normalized_identity.get("filters") or [])
    validate_filter_list_complexity(filters)
    analyzed = BaseQueryBuilder.analyze_bounded_datetime_filters(
        filters,
        strict=True,
    )

    retained: list[dict[str, Any]] = []
    for item in filters:
        column_id = item.get("column_id")
        if column_id not in {"created_at", "start_time"} or (
            (item.get("filter_config") or {}).get("col_type") == "SPAN_ATTRIBUTE"
        ):
            retained.append(item)

    if not analyzed.empty:
        for exclusion_start, exclusion_end in analyzed.exclusions:
            # Complements are meaningful only inside the frozen positive base.
            # Clamp and merge via the analyzer so logically equivalent
            # not_equals/not_between spellings share one exact job identity.
            lower = max(analyzed.start, exclusion_start)
            upper = min(analyzed.end, exclusion_end)
            if lower >= upper:
                continue
            retained.append(
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "datetime",
                        "filter_op": "not_between",
                        "filter_value": [
                            _utc_filter_bound(lower),
                            _utc_filter_bound(upper),
                        ],
                    },
                }
            )

    retained.append(
        {
            "column_id": "created_at",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    _utc_filter_bound(analyzed.start),
                    _utc_filter_bound(analyzed.end),
                ],
            },
        }
    )
    normalized_identity["filters"] = normalize_filter_conjunction(retained)
    return normalized_identity


def raw_observe_identity_key(namespace: str, identity: Any, suffix: str) -> str | None:
    """Address a side record of one raw (unfrozen) Observe request's scope.

    ``None`` when the identity carries no filters to key the scope by.
    """

    normalized_identity = normalized_snapshot_identity(identity)
    if (
        not isinstance(normalized_identity, dict)
        or "filters" not in normalized_identity
    ):
        return None
    from tracer.services.clickhouse.list_cursor import normalize_filter_conjunction

    normalized_identity["filters"] = normalize_filter_conjunction(
        normalized_identity.get("filters") or []
    )
    return f"{snapshot_cache_key(namespace, normalized_identity)}:{suffix}"


def _observe_identity_alias_key(namespace: str, identity: Any) -> str | None:
    """Address the frozen window chosen for one stable raw Observe request."""

    return raw_observe_identity_key(namespace, identity, "frozen-identity")


def _resolve_exact_observe_identity(
    namespace: str,
    identity: Any,
    *,
    refresh: bool,
) -> tuple[Any, Any | None]:
    """Reuse one frozen default window until an explicit aggregate refresh.

    Without this alias, a no-filter polling request would derive a new
    microsecond-level upper bound on every poll and never observe the worker it
    originally scheduled. The alias is cache-only, tenant-keyed, and contains
    no query result or database state.
    """

    frozen_identity = normalize_exact_observe_identity(identity)
    alias_key = _observe_identity_alias_key(namespace, identity)
    if alias_key is None:
        return frozen_identity, None

    prior_identity = None
    try:
        cached = cache.get(alias_key)
        if isinstance(cached, dict):
            prior_identity = normalized_snapshot_identity(cached)
            if not refresh:
                return prior_identity, None

            # Refresh-capable clients poll the same endpoint while the exact
            # worker is running. Do not advance the frozen time window on each
            # of those polls: the worker would publish under the prior key
            # after the alias had already moved, leaving every poll pending.
            # The snapshot check also closes the small race between publishing
            # the alias and acquiring the atomic refresh claim.
            prior_state = exact_refresh_state(namespace, prior_identity)
            if prior_state == "running" or (
                prior_state is None
                and read_exact_snapshot(namespace, prior_identity) is None
            ):
                return prior_identity, None

        timeout = _ttl_seconds()
        if refresh:
            cache.set(alias_key, frozen_identity, timeout=timeout)
            stale_identity = (
                prior_identity if prior_identity != frozen_identity else None
            )
            return frozen_identity, stale_identity

        if cache.add(alias_key, frozen_identity, timeout=timeout):
            return frozen_identity, None
        winner = cache.get(alias_key)
        if isinstance(winner, dict):
            return normalized_snapshot_identity(winner), None
    except Exception:
        logger.warning(
            "exact_aggregation_frozen_identity_alias_failed",
            namespace=namespace,
            exc_info=True,
        )
    return frozen_identity, None


def _refresh_lock_key(namespace: str, identity: Any) -> str:
    return f"{snapshot_cache_key(namespace, identity)}:refresh-lock"


def _refresh_state_key(namespace: str, identity: Any) -> str:
    return f"{snapshot_cache_key(namespace, identity)}:refresh-state"


def _refresh_reconcile_key(namespace: str, identity: Any) -> str:
    return f"{snapshot_cache_key(namespace, identity)}:refresh-reconcile"


def _revalidation_token_key(namespace: str, identity: Any) -> str:
    """The token of the latest automatic open-window revalidation claim.

    A side key, not a field in the refresh state: the dispatch/running state
    records are compared by value (Lua and fallback compare-and-set), so an
    extra field there would break those fences.
    """

    return f"{snapshot_cache_key(namespace, identity)}:revalidate-token"


def _carry_exact_snapshot_to_refreshed_identity(
    namespace: str,
    source_identity: Any,
    destination_identity: Any,
) -> None:
    """Keep the last exact payload visible while a new frozen window refreshes."""

    source_key = snapshot_cache_key(namespace, source_identity)
    destination_key = snapshot_cache_key(namespace, destination_identity)
    if source_key == destination_key:
        return
    try:
        stored = cache.get(source_key)
        if not isinstance(stored, dict) or stored.get("v") != _CACHE_VERSION:
            return
        cache.add(destination_key, stored, timeout=_ttl_seconds())
    except Exception:
        logger.warning(
            "exact_aggregation_snapshot_carry_failed",
            namespace=namespace,
            exc_info=True,
        )


def _admission_scope(identity: Any) -> str | None:
    if not isinstance(identity, dict):
        return None
    for field in ("project_id", "workspace_id", "organization_id"):
        value = identity.get(field)
        if value not in (None, ""):
            return f"{field}:{value}"
    return None


def _scope_admission_key(identity: Any) -> str | None:
    scope = _admission_scope(identity)
    if scope is None:
        return None
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()
    return f"exact-aggregation:v{_SCOPE_ADMISSION_KEY_VERSION}:scope-admission:{digest}"


def _max_inflight_per_scope() -> int:
    configured = int(
        getattr(
            settings,
            "EXACT_AGGREGATION_MAX_INFLIGHT_PER_SCOPE",
            _DEFAULT_MAX_INFLIGHT_PER_SCOPE,
        )
    )
    # This is a safety boundary, not an unbounded deployment tuning knob.
    return min(16, max(1, configured))


def _scope_admission_timeout(members: dict[str, int], now_ms: int) -> int:
    latest_expiry_ms = max(members.values(), default=now_ms)
    remaining_seconds = max(1, (latest_expiry_ms - now_ms + 999) // 1_000)
    return remaining_seconds + 5 * 60


def _admission_ceiling(limit: int | None) -> int:
    """The per-scope ceiling, lowered (never raised) by ``limit``."""

    ceiling = _max_inflight_per_scope()
    if limit is None:
        return ceiling
    return max(1, min(ceiling, int(limit)))


def _live_admission_members(raw_members: Any, now_ms: int) -> dict[str, int]:
    """Fallback-store members whose lease is later than now (the claim script's rule)."""

    members = raw_members if isinstance(raw_members, dict) else {}
    return {
        member: expiry
        for member, expiry in members.items()
        if isinstance(expiry, int) and expiry > now_ms
    }


def _revalidation_admission_limit() -> int:
    """Admission ceiling for a background revalidation: leave one slot free."""

    return max(1, _max_inflight_per_scope() - 1)


def _scope_admission_has_capacity(identity: Any, *, limit: int) -> bool:
    """Read-only: whether the scope has fewer than ``limit`` live jobs now.

    A cheap pre-check, not the admission itself: the atomic claim still
    decides. On any cache error it answers True and lets that claim decide
    (and fail closed).
    """

    admission_key = _scope_admission_key(identity)
    if admission_key is None:
        return True
    max_inflight = _admission_ceiling(limit)
    now_ms = int(time.time() * 1000)
    try:
        redis_client = _redis_cache_client()
        if redis_client is not None:
            raw_client = redis_client.get_client(write=True)
            # Live members have an expiry score later than now (the claim
            # script drops scores <= now before counting).
            live = raw_client.zcount(
                redis_client.make_key(admission_key), f"({now_ms}", "+inf"
            )
            return int(live) < max_inflight
        live = _live_admission_members(cache.get(admission_key), now_ms)
        return len(live) < max_inflight
    except Exception:
        logger.warning(
            "exact_aggregation_scope_admission_probe_failed",
            exc_info=True,
        )
        return True


def _claim_exact_refresh_admission(
    identity: Any,
    token: str,
    *,
    lease_seconds: int,
    limit: int | None = None,
) -> bool:
    """Admit a bounded number of distinct exact jobs per tenant scope.

    ``limit`` lowers the ceiling for one claim (never raises it above the
    per-scope maximum); a revalidation uses it to keep a slot for foreground
    work.
    """

    admission_key = _scope_admission_key(identity)
    if admission_key is None:
        return True
    max_inflight = _admission_ceiling(limit)
    now_ms = int(time.time() * 1000)
    expiry_ms = now_ms + lease_seconds * 1000
    ttl_margin_ms = 5 * 60 * 1000
    try:
        redis_client = _redis_cache_client()
        if redis_client is not None:
            raw_client = redis_client.get_client(write=True)
            return bool(
                raw_client.eval(
                    _REDIS_CLAIM_SCOPE_ADMISSION_SCRIPT,
                    1,
                    redis_client.make_key(admission_key),
                    now_ms,
                    token,
                    expiry_ms,
                    ttl_margin_ms,
                    max_inflight,
                )
            )

        with _CACHE_FENCE_FALLBACK_LOCK:
            members = _live_admission_members(cache.get(admission_key), now_ms)
            if token not in members and len(members) >= max_inflight:
                cache.set(
                    admission_key,
                    members,
                    timeout=_scope_admission_timeout(members, now_ms),
                )
                return False
            members[token] = expiry_ms
            cache.set(
                admission_key,
                members,
                timeout=_scope_admission_timeout(members, now_ms),
            )
            return True
    except Exception:
        logger.warning(
            "exact_aggregation_scope_admission_failed",
            exc_info=True,
        )
        # Fail closed: cache impairment must not remove the ClickHouse load
        # boundary for a cold, potentially hour-long aggregation.
        return False


def _renew_exact_refresh_admission(
    identity: Any,
    token: str,
    *,
    lease_seconds: int,
) -> bool:
    admission_key = _scope_admission_key(identity)
    if admission_key is None:
        return True
    now_ms = int(time.time() * 1000)
    expiry_ms = now_ms + lease_seconds * 1000
    ttl_margin_ms = 5 * 60 * 1000
    try:
        redis_client = _redis_cache_client()
        if redis_client is not None:
            raw_client = redis_client.get_client(write=True)
            return bool(
                raw_client.eval(
                    _REDIS_RENEW_SCOPE_ADMISSION_SCRIPT,
                    1,
                    redis_client.make_key(admission_key),
                    now_ms,
                    token,
                    expiry_ms,
                    ttl_margin_ms,
                )
            )

        with _CACHE_FENCE_FALLBACK_LOCK:
            members = _live_admission_members(cache.get(admission_key), now_ms)
            if token not in members:
                return False
            members[token] = expiry_ms
            cache.set(
                admission_key,
                members,
                timeout=_scope_admission_timeout(members, now_ms),
            )
            return True
    except Exception:
        logger.warning(
            "exact_aggregation_scope_admission_renew_failed",
            exc_info=True,
        )
        return False


def _release_exact_refresh_admission(identity: Any, token: str) -> None:
    admission_key = _scope_admission_key(identity)
    if admission_key is None or not token:
        return
    try:
        redis_client = _redis_cache_client()
        if redis_client is not None:
            raw_client = redis_client.get_client(write=True)
            raw_client.eval(
                _REDIS_RELEASE_SCOPE_ADMISSION_SCRIPT,
                1,
                redis_client.make_key(admission_key),
                token,
            )
            return

        with _CACHE_FENCE_FALLBACK_LOCK:
            raw_members = cache.get(admission_key)
            members = dict(raw_members) if isinstance(raw_members, dict) else {}
            members.pop(token, None)
            if members:
                now_ms = int(time.time() * 1000)
                cache.set(
                    admission_key,
                    members,
                    timeout=_scope_admission_timeout(members, now_ms),
                )
            else:
                cache.delete(admission_key)
    except Exception:
        logger.warning(
            "exact_aggregation_scope_admission_release_failed",
            exc_info=True,
        )


def _ttl_seconds() -> int | None:
    configured = getattr(
        settings,
        "EXACT_AGGREGATION_SNAPSHOT_TTL_SECONDS",
        _DEFAULT_TTL_SECONDS,
    )
    if configured is None:
        return None
    return max(1, int(configured))


def _refresh_lock_seconds() -> int:
    return max(
        _DEFAULT_REFRESH_LOCK_SECONDS,
        int(
            getattr(
                settings,
                "EXACT_AGGREGATION_REFRESH_LOCK_SECONDS",
                _DEFAULT_REFRESH_LOCK_SECONDS,
            )
        ),
    )


def _refresh_dispatch_seconds() -> int:
    return max(
        _DEFAULT_REFRESH_DISPATCH_SECONDS,
        int(
            getattr(
                settings,
                "EXACT_AGGREGATION_REFRESH_DISPATCH_SECONDS",
                _DEFAULT_REFRESH_DISPATCH_SECONDS,
            )
        ),
    )


def refresh_failure_seconds() -> int:
    return max(
        30,
        int(
            getattr(
                settings,
                "EXACT_AGGREGATION_REFRESH_FAILURE_SECONDS",
                _DEFAULT_REFRESH_FAILURE_SECONDS,
            )
        ),
    )


def _revalidation_floor_seconds() -> int | None:
    """Minimum snapshot age before an open-window hit refreshes, or ``None`` (off)."""

    configured = getattr(
        settings,
        "EXACT_AGGREGATION_REVALIDATE_AFTER_SECONDS",
        _DEFAULT_REVALIDATE_AFTER_SECONDS,
    )
    if configured is None:
        return None
    seconds = int(configured)
    if seconds <= 0:
        return None
    return max(_MIN_REVALIDATE_AFTER_SECONDS, seconds)


def _refresh_reconcile_seconds() -> int:
    return max(
        1,
        int(
            getattr(
                settings,
                "EXACT_AGGREGATION_REFRESH_RECONCILE_SECONDS",
                _DEFAULT_REFRESH_RECONCILE_SECONDS,
            )
        ),
    )


def _refresh_status_timeout_seconds() -> float:
    configured = float(
        getattr(
            settings,
            "EXACT_AGGREGATION_REFRESH_STATUS_TIMEOUT_SECONDS",
            _DEFAULT_REFRESH_STATUS_TIMEOUT_SECONDS,
        )
    )
    # Reconciliation runs on an HTTP poll. Keep Temporal impairment bounded.
    return min(2.0, max(0.05, configured))


def _configured_exact_aggregation_task_queue() -> str | None:
    """Return an explicitly supported queue, or fail closed on configuration drift."""

    task_queue = str(
        getattr(settings, "EXACT_AGGREGATION_TASK_QUEUE", "tasks_xl")
    ).strip()
    if task_queue in _ALLOWED_EXACT_AGGREGATION_TASK_QUEUES:
        return task_queue
    logger.error(
        "exact_aggregation_task_queue_invalid",
        configured_queue=task_queue,
        allowed_queues=sorted(_ALLOWED_EXACT_AGGREGATION_TASK_QUEUES),
    )
    return None


def _decorate(snapshot: ExactAggregationSnapshot) -> Any:
    payload = deepcopy(snapshot.payload)
    metadata = {
        "query_completed_at": snapshot.completed_at,
        "query_cached": snapshot.cache_hit,
    }
    if isinstance(payload, dict):
        payload.update(metadata)
        return payload
    if isinstance(payload, list):
        return [
            {**item, **metadata} if isinstance(item, dict) else item for item in payload
        ]
    raise TypeError("exact aggregation payload must be a mapping or list")


def read_exact_snapshot(namespace: str, identity: Any) -> Any | None:
    key = snapshot_cache_key(namespace, identity)
    try:
        stored = cache.get(key)
    except Exception:
        logger.warning(
            "exact_aggregation_cache_get_failed",
            namespace=namespace,
            exc_info=True,
        )
        return None
    if not isinstance(stored, dict) or stored.get("v") != _CACHE_VERSION:
        return None
    completed_at = stored.get("completed_at")
    payload = stored.get("payload")
    if not isinstance(completed_at, str) or not isinstance(payload, (dict, list)):
        return None
    return _decorate(
        ExactAggregationSnapshot(
            payload=payload,
            completed_at=completed_at,
            cache_hit=True,
        )
    )


def publish_exact_snapshot(namespace: str, identity: Any, payload: Any) -> Any:
    """Atomically replace the prior snapshot after exactness was proven."""

    if not exact_payload_is_complete(payload):
        raise ValueError("only complete exact aggregation payloads may be published")
    completed_at = datetime.now(UTC).isoformat()
    stored = {
        "v": _CACHE_VERSION,
        "completed_at": completed_at,
        # Do not recursively persist response-only cache metadata.
        "payload": _without_snapshot_metadata(payload),
    }
    try:
        cache.set(
            snapshot_cache_key(namespace, identity),
            stored,
            timeout=_ttl_seconds(),
        )
    except Exception:
        # Cache availability must not turn a completed exact database read into
        # an API failure.  The caller still receives the exact fresh payload.
        logger.warning(
            "exact_aggregation_cache_set_failed",
            namespace=namespace,
            exc_info=True,
        )
    return _decorate(
        ExactAggregationSnapshot(
            payload=stored["payload"],
            completed_at=completed_at,
            cache_hit=False,
        )
    )


def _redis_cache_client() -> Any | None:
    """Return django-redis' client adapter, or ``None`` for local test caches."""

    try:
        return cache.client
    except AttributeError:
        return None


def _publish_fenced_snapshot(
    namespace: str,
    identity: Any,
    token: str,
    stored: dict[str, Any],
) -> bool:
    """Atomically publish and release only while ``token`` owns the claim."""

    lock_key = _refresh_lock_key(namespace, identity)
    snapshot_key = snapshot_cache_key(namespace, identity)
    state_key = _refresh_state_key(namespace, identity)
    redis_client = _redis_cache_client()
    if redis_client is None:
        # LocMemCache is used by unit tests. Its operations become one fenced
        # critical section under this process-local lock.
        with _CACHE_FENCE_FALLBACK_LOCK:
            if cache.get(lock_key) != token:
                return False
            cache.set(snapshot_key, stored, timeout=_ttl_seconds())
            cache.delete(state_key)
            cache.delete(lock_key)
            return True

    raw_client = redis_client.get_client(write=True)
    ttl_seconds = _ttl_seconds()
    ttl_ms = -1 if ttl_seconds is None else ttl_seconds * 1000
    return bool(
        raw_client.eval(
            _REDIS_FENCED_PUBLISH_SCRIPT,
            3,
            redis_client.make_key(lock_key),
            redis_client.make_key(snapshot_key),
            redis_client.make_key(state_key),
            redis_client.encode(token),
            redis_client.encode(stored),
            ttl_ms,
        )
    )


def publish_exact_snapshot_for_refresh(
    namespace: str,
    identity: Any,
    payload: Any,
    token: str,
) -> Any | None:
    """Token-fenced exact publication for at-least-once background workers."""

    if not exact_payload_is_complete(payload):
        raise ValueError("only complete exact aggregation payloads may be published")
    completed_at = datetime.now(UTC).isoformat()
    stored = {
        "v": _CACHE_VERSION,
        "completed_at": completed_at,
        "payload": _without_snapshot_metadata(payload),
    }
    if not _publish_fenced_snapshot(namespace, identity, token, stored):
        return None
    return _decorate(
        ExactAggregationSnapshot(
            payload=stored["payload"],
            completed_at=completed_at,
            cache_hit=False,
        )
    )


def _without_snapshot_metadata(payload: Any) -> Any:
    copied = deepcopy(payload)
    if isinstance(copied, dict):
        copied.pop("query_completed_at", None)
        copied.pop("query_cached", None)
        copied.pop("query_refreshing", None)
        copied.pop("query_refresh_failed", None)
    elif isinstance(copied, list):
        for item in copied:
            if isinstance(item, dict):
                item.pop("query_completed_at", None)
                item.pop("query_cached", None)
                item.pop("query_refreshing", None)
                item.pop("query_refresh_failed", None)
    return copied


def exact_payload_is_complete(payload: Any) -> bool:
    """Return true only when every declared aggregation series is exact."""

    if isinstance(payload, list):
        # An empty exact multi-series result is a valid completed aggregation.
        return all(exact_payload_is_complete(item) for item in payload)
    if not isinstance(payload, dict):
        return False
    if payload.get("query_complete") is not True:
        return False
    if payload.get("query_status") != "complete":
        return False
    # Exactness is fail-closed: producers must explicitly attest that the
    # completed payload was not sampled.  A missing/null/non-boolean marker is
    # not sufficient to publish an aggregation snapshot.
    if payload.get("query_sampled") is not False or payload.get("error"):
        return False

    metrics = payload.get("metrics")
    if isinstance(metrics, list):
        return all(exact_payload_is_complete(metric) for metric in metrics)

    return True


def mark_refresh_failed(payload: Any) -> Any:
    copied = deepcopy(payload)
    if isinstance(copied, dict):
        copied["query_refresh_failed"] = True
    elif isinstance(copied, list):
        for item in copied:
            if isinstance(item, dict):
                item["query_refresh_failed"] = True
    return copied


def _decorate_refresh_state(payload: Any, status: str | None) -> Any:
    copied = deepcopy(payload)
    metadata: dict[str, Any] = {}
    if status == "running":
        metadata["query_refreshing"] = True
        metadata["query_refresh_failed"] = False
    elif status == "failed":
        metadata["query_refreshing"] = False
        metadata["query_refresh_failed"] = True
    else:
        metadata["query_refreshing"] = False
        metadata["query_refresh_failed"] = False
    if isinstance(copied, dict):
        copied.update(metadata)
    elif isinstance(copied, list):
        for item in copied:
            if isinstance(item, dict):
                item.update(metadata)
    return copied


def _exact_refresh_state_record(
    namespace: str,
    identity: Any,
) -> dict[str, Any] | None:
    try:
        state = cache.get(_refresh_state_key(namespace, identity))
    except Exception:
        logger.warning(
            "exact_aggregation_refresh_state_get_failed",
            namespace=namespace,
            exc_info=True,
        )
        return None
    return state if isinstance(state, dict) else None


def exact_refresh_state(namespace: str, identity: Any) -> str | None:
    """Return the public refresh state without exposing task or cache details."""

    state = _exact_refresh_state_record(namespace, identity)
    if state is not None and state.get("status") in {"running", "failed"}:
        return str(state["status"])
    return None


def _release_partial_refresh_claim_fallback(
    lock_key: str,
    state_key: str,
    token: str,
) -> None:
    """Best-effort rollback for process-local caches after a partial claim write."""

    try:
        if cache.get(lock_key) != token:
            return
    except Exception:
        # Without a successful ownership read, deleting could clear a newer
        # owner's lock. Let the bounded dispatch lease expire instead.
        return

    try:
        stored_state = cache.get(state_key)
    except Exception:
        stored_state = None
    if isinstance(stored_state, dict) and stored_state.get("token") == token:
        try:
            cache.delete(state_key)
        except Exception:
            pass

    # Re-read immediately before deletion so a replacement observed during
    # cleanup is never cleared. Redis deployments never use this fallback;
    # their claim is one Lua operation.
    try:
        if cache.get(lock_key) == token:
            cache.delete(lock_key)
    except Exception:
        pass


def _recover_ambiguous_redis_refresh_claim(
    *,
    namespace: str,
    token: str,
    raw_client: Any,
    redis_lock_key: Any,
    redis_state_key: Any,
    encoded_token: Any,
    encoded_dispatch_state: Any,
) -> str | None:
    """Resolve an indeterminate Redis claim response without overlapping work.

    A connection can fail after Redis has completed the Lua script but before
    the client receives its result.  Reading both primary-backed keys lets the
    caller continue with the exact token when the whole claim landed.  A
    partial write is removed only through value-fenced Lua comparisons.  If
    Redis is still unavailable, the bounded dispatch TTL is the safe fallback.
    """

    try:
        stored_lock, stored_state = raw_client.mget(
            redis_lock_key,
            redis_state_key,
        )
    except Exception:
        logger.warning(
            "exact_aggregation_refresh_claim_readback_failed",
            namespace=namespace,
            exc_info=True,
        )
        return None

    lock_owned = stored_lock == encoded_token
    state_owned = stored_state == encoded_dispatch_state
    if lock_owned and state_owned:
        logger.warning(
            "exact_aggregation_refresh_claim_recovered",
            namespace=namespace,
        )
        return token

    if not lock_owned and not state_owned:
        return None

    try:
        raw_client.eval(
            _REDIS_FENCED_ROLLBACK_REFRESH_CLAIM_SCRIPT,
            2,
            redis_lock_key,
            redis_state_key,
            encoded_token,
            encoded_dispatch_state,
        )
    except Exception:
        logger.warning(
            "exact_aggregation_refresh_claim_rollback_failed",
            namespace=namespace,
            exc_info=True,
        )
    return None


def begin_exact_refresh(namespace: str, identity: Any) -> str | None:
    """Atomically claim the pre-activity dispatch lease for a query.

    The worker must call :func:`activate_exact_refresh` before doing any work.
    If no compatible Temporal worker starts the activity, both keys expire and
    the next ordinary poll can safely enqueue a fresh, uniquely fenced claim.
    """

    token = uuid4().hex
    dispatch_seconds = _refresh_dispatch_seconds()
    lock_key = _refresh_lock_key(namespace, identity)
    state_key = _refresh_state_key(namespace, identity)
    dispatch_state = {
        "status": "running",
        "token": token,
        "phase": "dispatch",
    }
    try:
        redis_client = _redis_cache_client()
        if redis_client is not None:
            raw_client = redis_client.get_client(write=True)
            redis_lock_key = redis_client.make_key(lock_key)
            redis_state_key = redis_client.make_key(state_key)
            encoded_token = redis_client.encode(token)
            encoded_dispatch_state = redis_client.encode(dispatch_state)
            try:
                claimed = raw_client.eval(
                    _REDIS_ATOMIC_REFRESH_CLAIM_SCRIPT,
                    2,
                    redis_lock_key,
                    redis_state_key,
                    encoded_token,
                    encoded_dispatch_state,
                    dispatch_seconds * 1000,
                )
            except Exception:
                logger.warning(
                    "exact_aggregation_refresh_claim_failed",
                    namespace=namespace,
                    exc_info=True,
                )
                return _recover_ambiguous_redis_refresh_claim(
                    namespace=namespace,
                    token=token,
                    raw_client=raw_client,
                    redis_lock_key=redis_lock_key,
                    redis_state_key=redis_state_key,
                    encoded_token=encoded_token,
                    encoded_dispatch_state=encoded_dispatch_state,
                )
            return token if claimed else None

        # LocMemCache and the test caches do not expose a Redis client. Keep
        # their two operations in one process-local critical section, verify
        # both writes, and token-fence rollback if the state write is partial.
        with _CACHE_FENCE_FALLBACK_LOCK:
            if not cache.add(lock_key, token, timeout=dispatch_seconds):
                return None
            try:
                cache.set(state_key, dispatch_state, timeout=dispatch_seconds)
                if (
                    cache.get(lock_key) == token
                    and cache.get(state_key) == dispatch_state
                ):
                    return token
            except Exception:
                logger.warning(
                    "exact_aggregation_refresh_claim_state_write_failed",
                    namespace=namespace,
                    exc_info=True,
                )

            # Cleanup intentionally re-reads ownership. A backend failure may
            # have happened after writing, or the lease may already have been
            # replaced; never delete a replacement.
            _release_partial_refresh_claim_fallback(lock_key, state_key, token)
            return None
    except Exception:
        logger.warning(
            "exact_aggregation_refresh_claim_failed",
            namespace=namespace,
            exc_info=True,
        )
        return None


def record_exact_refresh_dispatch(
    namespace: str,
    identity: Any,
    token: str,
    workflow_id: str,
) -> bool:
    """Attach Temporal lifecycle evidence to a current dispatch claim.

    The compare-and-set deliberately accepts only the initial dispatch state.
    An exceptionally fast activity may already have promoted or finished the
    claim by the time ``apply_async`` returns; in that case this must not move
    the state backwards or resurrect its lease.
    """

    if not token or not isinstance(workflow_id, str) or not workflow_id:
        return False
    dispatch_seconds = _refresh_dispatch_seconds()
    initial_state = {"status": "running", "token": token, "phase": "dispatch"}
    recorded_state = {
        **initial_state,
        "workflow_id": workflow_id,
    }
    try:
        lock_key = _refresh_lock_key(namespace, identity)
        state_key = _refresh_state_key(namespace, identity)
        redis_client = _redis_cache_client()
        if redis_client is None:
            with _CACHE_FENCE_FALLBACK_LOCK:
                if cache.get(lock_key) != token:
                    return False
                if cache.get(state_key) != initial_state:
                    return False
                cache.set(lock_key, token, timeout=dispatch_seconds)
                cache.set(state_key, recorded_state, timeout=dispatch_seconds)
                return True

        raw_client = redis_client.get_client(write=True)
        return bool(
            raw_client.eval(
                _REDIS_FENCED_RECORD_DISPATCH_SCRIPT,
                2,
                redis_client.make_key(lock_key),
                redis_client.make_key(state_key),
                redis_client.encode(token),
                redis_client.encode(initial_state),
                redis_client.encode(recorded_state),
                dispatch_seconds * 1000,
            )
        )
    except Exception:
        logger.warning(
            "exact_aggregation_refresh_dispatch_record_failed",
            namespace=namespace,
            exc_info=True,
        )
        return False


def activate_exact_refresh(namespace: str, identity: Any, token: str) -> bool:
    """Promote a current dispatch lease to the long running-query lease.

    Promotion is token-fenced and atomic on Redis.  An activity delivered after
    its dispatch lease expired therefore exits before querying ClickHouse, even
    if a later poll has already claimed and queued a replacement refresh.
    """

    if not token:
        return False
    try:
        lock_key = _refresh_lock_key(namespace, identity)
        state_key = _refresh_state_key(namespace, identity)
        running_state = {"status": "running", "token": token, "phase": "running"}
        running_seconds = _refresh_lock_seconds()
        redis_client = _redis_cache_client()
        if redis_client is None:
            with _CACHE_FENCE_FALLBACK_LOCK:
                if cache.get(lock_key) != token:
                    return False
                cache.set(lock_key, token, timeout=running_seconds)
                cache.set(state_key, running_state, timeout=running_seconds)
                activated = True
        else:
            raw_client = redis_client.get_client(write=True)
            activated = bool(
                raw_client.eval(
                    _REDIS_FENCED_ACTIVATE_SCRIPT,
                    2,
                    redis_client.make_key(lock_key),
                    redis_client.make_key(state_key),
                    redis_client.encode(token),
                    running_seconds * 1000,
                    redis_client.encode(running_state),
                )
            )
        if not activated:
            return False
        return _renew_exact_refresh_admission(
            identity,
            token,
            lease_seconds=running_seconds,
        ) or _claim_exact_refresh_admission(
            identity,
            token,
            lease_seconds=running_seconds,
        )
    except Exception:
        logger.warning(
            "exact_aggregation_refresh_activation_failed",
            namespace=namespace,
            exc_info=True,
        )
        return False


def finish_exact_refresh(
    namespace: str,
    identity: Any,
    token: str,
    *,
    succeeded: bool,
) -> None:
    """Release a refresh claim and record only sanitized terminal state."""

    try:
        lock_key = _refresh_lock_key(namespace, identity)
        state_key = _refresh_state_key(namespace, identity)
        failed_state = {"status": "failed", "token": token}
        redis_client = _redis_cache_client()
        if redis_client is None:
            with _CACHE_FENCE_FALLBACK_LOCK:
                if cache.get(lock_key) != token:
                    return
                if succeeded:
                    cache.delete(state_key)
                else:
                    cache.set(
                        state_key,
                        failed_state,
                        timeout=refresh_failure_seconds(),
                    )
                cache.delete(lock_key)
            return

        raw_client = redis_client.get_client(write=True)
        raw_client.eval(
            _REDIS_FENCED_FINISH_SCRIPT,
            2,
            redis_client.make_key(lock_key),
            redis_client.make_key(state_key),
            redis_client.encode(token),
            1 if succeeded else 0,
            redis_client.encode(failed_state),
            refresh_failure_seconds() * 1000,
        )
    except Exception:
        logger.warning(
            "exact_aggregation_refresh_finish_failed",
            namespace=namespace,
            exc_info=True,
        )
    finally:
        _release_exact_refresh_admission(identity, token)


def refresh_claim_is_current(namespace: str, identity: Any, token: str) -> bool:
    """Return whether ``token`` still owns this refresh without exposing it."""

    if not token:
        return False
    try:
        return cache.get(_refresh_lock_key(namespace, identity)) == token
    except Exception:
        logger.warning(
            "exact_aggregation_refresh_claim_check_failed",
            namespace=namespace,
            exc_info=True,
        )
        return False


def _exact_refresh_workflow_task_id(refresh_token: str) -> str:
    """Derive a repeatable opaque Temporal id for one claimed refresh."""

    digest = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()[:32]
    return f"exact-aggregation-{digest}"


_TERMINAL_WORKFLOW_STATUSES = {
    "CANCELED",
    "COMPLETED",
    "FAILED",
    "TERMINATED",
    "TIMED_OUT",
}


def _release_exact_refresh_dispatch(
    namespace: str,
    identity: Any,
    token: str,
    expected_state: dict[str, Any],
) -> bool:
    """Atomically release only the exact dispatch phase that was inspected."""

    try:
        lock_key = _refresh_lock_key(namespace, identity)
        state_key = _refresh_state_key(namespace, identity)
        redis_client = _redis_cache_client()
        if redis_client is None:
            with _CACHE_FENCE_FALLBACK_LOCK:
                if cache.get(lock_key) != token:
                    return False
                if cache.get(state_key) != expected_state:
                    return False
                cache.delete(state_key)
                cache.delete(lock_key)
                return True

        raw_client = redis_client.get_client(write=True)
        return bool(
            raw_client.eval(
                _REDIS_FENCED_RELEASE_DISPATCH_SCRIPT,
                2,
                redis_client.make_key(lock_key),
                redis_client.make_key(state_key),
                redis_client.encode(token),
                redis_client.encode(expected_state),
            )
        )
    except Exception:
        logger.warning(
            "exact_aggregation_terminal_dispatch_release_failed",
            namespace=namespace,
            exc_info=True,
        )
        return False


def _release_terminal_dispatch_claim(namespace: str, identity: Any) -> bool:
    """Release a pre-activity claim only after Temporal proves it is terminal."""

    state = _exact_refresh_state_record(namespace, identity)
    if (
        state is None
        or state.get("status") != "running"
        or state.get("phase") != "dispatch"
    ):
        return False
    token = state.get("token")
    workflow_id = state.get("workflow_id")
    if not isinstance(token, str) or not isinstance(workflow_id, str):
        return False
    try:
        if not cache.add(
            _refresh_reconcile_key(namespace, identity),
            token,
            timeout=_refresh_reconcile_seconds(),
        ):
            return False

        from tfc.temporal.common.client import get_workflow_status_sync

        workflow_status = get_workflow_status_sync(
            workflow_id,
            timeout_seconds=_refresh_status_timeout_seconds(),
        )
        status_name = (
            workflow_status.get("status_name")
            if isinstance(workflow_status, dict)
            else None
        )
        if status_name not in _TERMINAL_WORKFLOW_STATUSES:
            return False

        # Compare both token and the complete dispatch state. A status result
        # that races with activity promotion/publication therefore cannot clear
        # that running lease or enqueue a redundant replacement.
        released = _release_exact_refresh_dispatch(
            namespace,
            identity,
            token,
            state,
        )
        if released:
            _release_exact_refresh_admission(identity, token)
            logger.info(
                "exact_aggregation_terminal_dispatch_released",
                namespace=namespace,
                workflow_status=status_name,
            )
        return released
    except Exception:
        logger.warning(
            "exact_aggregation_refresh_reconcile_failed",
            namespace=namespace,
            exc_info=True,
        )
        return False


def _parse_utc_instant(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _frozen_window_end(identity: Any) -> datetime | None:
    """The upper bound of the ``created_at`` window a frozen Observe identity holds.

    ``normalize_exact_observe_identity`` writes exactly one SYSTEM_METRIC
    ``created_at between`` item; anything else has no window to judge.
    """

    if not isinstance(identity, dict):
        return None
    for item in identity.get("filters") or []:
        if not isinstance(item, dict) or item.get("column_id") != "created_at":
            continue
        config = item.get("filter_config") or {}
        if (
            config.get("filter_op") != "between"
            or config.get("col_type") != "SYSTEM_METRIC"
        ):
            continue
        bounds = config.get("filter_value")
        if isinstance(bounds, (list, tuple)) and len(bounds) == 2:
            return _parse_utc_instant(bounds[1])
    return None


def _remember_revalidation_token(namespace: str, identity: Any, token: str) -> None:
    """Mark ``token`` as an automatic revalidation for as long as its state lives."""

    try:
        cache.set(
            _revalidation_token_key(namespace, identity),
            token,
            timeout=_refresh_dispatch_seconds()
            + _refresh_lock_seconds()
            + refresh_failure_seconds(),
        )
    except Exception:
        logger.warning(
            "exact_aggregation_revalidation_token_write_failed",
            namespace=namespace,
            exc_info=True,
        )


def _served_refresh_state(
    namespace: str,
    identity: Any,
    state: str | None,
) -> str | None:
    """The refresh state to show on a served hit.

    A failed AUTOMATIC revalidation is not a failed read: nobody asked for it,
    and the hit is still exact for its window as of its ``completed_at``. It
    is served plain (the failed state still blocks a re-claim for its TTL, so
    it remains the backoff). A failed explicit refresh keeps
    ``query_refresh_failed``: the user asked for newer data and did not get it.
    """

    if state != "failed":
        return state
    record = _exact_refresh_state_record(namespace, identity)
    token = record.get("token") if isinstance(record, dict) else None
    if not token:
        return state
    try:
        automatic = cache.get(_revalidation_token_key(namespace, identity))
    except Exception:
        # Unknown provenance: report the failure rather than hide one the
        # user may have asked for.
        logger.warning(
            "exact_aggregation_revalidation_token_read_failed",
            namespace=namespace,
            exc_info=True,
        )
        return state
    return None if automatic == token else state


def _open_window_hit_is_due(namespace: str, identity: Any, snapshot: Any) -> bool:
    """Whether a served hit was computed while its window was open, long enough ago.

    Only the window's end matters: a snapshot completed after its window closed
    already holds every span that window can contain (late ingestion aside),
    while one completed before the end is missing what arrived since.
    """

    if namespace not in _OPEN_WINDOW_REVALIDATION_NAMESPACES:
        return False
    floor_seconds = _revalidation_floor_seconds()
    if floor_seconds is None or not isinstance(snapshot, dict):
        return False
    completed_at = _parse_utc_instant(snapshot.get("query_completed_at"))
    window_end = _frozen_window_end(identity)
    if completed_at is None or window_end is None or window_end <= completed_at:
        return False
    return (datetime.now(UTC) - completed_at).total_seconds() >= floor_seconds


def _enqueue_exact_refresh(
    namespace: str,
    identity: Any,
    token: str,
    task_queue: str,
) -> bool:
    """Hand one claimed refresh to Temporal; on failure record the failed state."""

    try:
        from temporalio.common import WorkflowIDConflictPolicy

        from tracer.tasks.exact_aggregation import (
            refresh_exact_aggregation_snapshot,
        )

        enqueue_result = refresh_exact_aggregation_snapshot.apply_async(
            kwargs={
                "namespace": namespace,
                "identity": identity,
                "refresh_token": token,
            },
            queue=task_queue,
            task_id=_exact_refresh_workflow_task_id(token),
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            # Keep the HTTP API boundary bounded if Temporal is impaired.
            # This timeout covers only workflow dispatch; accepted exact
            # reads retain their one-hour activity budget.
            dispatch_timeout_seconds=2.0,
        )
        workflow_id = getattr(enqueue_result, "id", None)
        if isinstance(workflow_id, str):
            record_exact_refresh_dispatch(namespace, identity, token, workflow_id)
    except Exception:
        logger.warning(
            "exact_aggregation_refresh_enqueue_failed",
            namespace=namespace,
            exc_info=True,
        )
        finish_exact_refresh(namespace, identity, token, succeeded=False)
        return False
    return True


def _revalidate_open_window_hit(
    namespace: str,
    identity: Any,
    previous: Any,
    read_servable: Callable[[Any], Any | None],
) -> Any:
    """Refresh the SAME identity behind a served open-window hit.

    The caller has already established that no refresh of this identity is
    running or recently failed. Every outcome serves ``previous`` (or the
    newer snapshot an eager worker just published); it is marked refreshing
    only when this request's claim was enqueued or another request's claim
    is persisted. There is no Temporal reconciliation here and no fall-through
    to the cold-miss scheduler, whose deferred-admission answer is "running"
    for a job that does not exist.
    """

    task_queue = _configured_exact_aggregation_task_queue()
    if task_queue is None:
        return _decorate_refresh_state(previous, None)
    admission_limit = _revalidation_admission_limit()
    if not _scope_admission_has_capacity(identity, limit=admission_limit):
        # The scope's spare slot is taken: serve the hit plain WITHOUT
        # claiming. Claiming first would publish a "running" state for a job
        # that is then refused, which concurrent polls report as refreshing
        # and which makes an explicit Reload in that window find the claim
        # taken and enqueue nothing.
        return _decorate_refresh_state(previous, None)
    token = begin_exact_refresh(namespace, identity)
    if token is None:
        # A concurrent visit won the claim (or the cache is impaired): report
        # only what is persisted.
        return _decorate_refresh_state(
            previous,
            _served_refresh_state(
                namespace, identity, exact_refresh_state(namespace, identity)
            ),
        )
    if not _claim_exact_refresh_admission(
        identity,
        token,
        lease_seconds=_refresh_dispatch_seconds(),
        limit=admission_limit,
    ):
        # A job took the slot between the pre-check and this claim (rare).
        # Foreground work holds the scope's spare slot. The hit is still exact
        # for its window as of its completed_at, and is served plain: nothing
        # retries until a later visit finds a slot free (see the admission
        # note at _DEFAULT_REVALIDATE_AFTER_SECONDS).
        finish_exact_refresh(namespace, identity, token, succeeded=True)
        return _decorate_refresh_state(previous, None)
    _remember_revalidation_token(namespace, identity, token)
    if not _enqueue_exact_refresh(namespace, identity, token, task_queue):
        # The failed state is the backoff: no visit retries for its TTL. It is
        # this automatic claim's failure, so the hit is served plain.
        return _decorate_refresh_state(
            previous,
            _served_refresh_state(
                namespace, identity, exact_refresh_state(namespace, identity)
            ),
        )
    current = read_servable(identity)
    current_state = exact_refresh_state(namespace, identity)
    if current is None:
        current = previous
    if current_state is None and current.get("query_completed_at") == previous.get(
        "query_completed_at"
    ):
        # Enqueued, but the state read was lost: this request owns the claim.
        current_state = "running"
    # A fast (or eager) worker may already have failed this automatic
    # refresh: the claimer sees the same plain hit every other viewer sees.
    return _decorate_refresh_state(
        current, _served_refresh_state(namespace, identity, current_state)
    )


def read_or_schedule_exact_snapshot(
    namespace: str,
    identity: Any,
    *,
    refresh: bool,
    pending_payload: Any,
    schedule_on_miss: bool = True,
    accept_snapshot: Callable[[Any], bool] | None = None,
    revalidate_open_window: bool = False,
) -> Any:
    """Serve an exact snapshot immediately and run slow refreshes out of band.

    A cache hit is never replaced by a pending response. A cold miss returns a
    non-chartable pending envelope. Failed cold jobs wait for another explicit
    refresh instead of being resubmitted by every polling request.
    ``accept_snapshot`` lets a caller reject a cached payload it cannot serve
    (for example one written by an older worker during a rolling deploy); a
    rejected snapshot is treated exactly as a miss on every read, including
    the re-read after a refresh is enqueued.

    ``revalidate_open_window`` (honoured only for the three Observe
    system-metric graph namespaces and the Agent Graph) refreshes a served hit of the same
    identity when its window was still open at ``completed_at``, the hit is
    older than ``EXACT_AGGREGATION_REVALIDATE_AFTER_SECONDS``, it passed
    ``accept_snapshot``, and no refresh of it is running or recently failed.
    The hit is served at once, marked ``query_refreshing`` only when a claim
    was taken; this applies to cache-only probes too, which therefore
    schedule whenever they mark a hit refreshing.
    """

    stale_identity = None
    if namespace.startswith("observe-"):
        normalized_identity, stale_identity = _resolve_exact_observe_identity(
            namespace,
            identity,
            refresh=refresh,
        )
    else:
        normalized_identity = normalized_snapshot_identity(identity)
    if stale_identity is not None:
        _carry_exact_snapshot_to_refreshed_identity(
            namespace,
            stale_identity,
            normalized_identity,
        )

    def read_servable(snapshot_identity: Any) -> Any | None:
        # Every read that can be served goes through the caller's guard: the
        # re-read after enqueueing too, since a rejected payload stays in the
        # cache (and may have been carried into this key) until a worker the
        # caller trusts overwrites it.
        snapshot = read_exact_snapshot(namespace, snapshot_identity)
        if (
            snapshot is not None
            and accept_snapshot is not None
            and not accept_snapshot(snapshot)
        ):
            return None
        return snapshot

    previous = read_servable(normalized_identity)
    if previous is None and stale_identity is not None:
        previous = read_servable(stale_identity)
    state = exact_refresh_state(namespace, normalized_identity)
    if (
        revalidate_open_window
        and previous is not None
        and not refresh
        and state is None
        and stale_identity is None
        and _open_window_hit_is_due(namespace, normalized_identity, previous)
    ):
        # ``previous`` came from ``normalized_identity`` itself (no refresh, so
        # no stale identity and no carry) and already passed the caller's guard.
        return _revalidate_open_window_hit(
            namespace,
            normalized_identity,
            previous,
            read_servable,
        )
    if previous is not None and not refresh:
        return _decorate_refresh_state(
            previous, _served_refresh_state(namespace, normalized_identity, state)
        )
    if previous is None and state == "failed" and not refresh:
        return _decorate_refresh_state(pending_payload, state)
    if not schedule_on_miss:
        # Interactive readers use this cache-only probe before attempting the
        # direct ClickHouse path.  A running refresh must suppress duplicate
        # foreground work, while a true cold miss must remain free to run
        # synchronously instead of being queued pre-emptively.
        return _decorate_refresh_state(
            previous if previous is not None else pending_payload,
            state,
        )

    task_queue = _configured_exact_aggregation_task_queue()
    if task_queue is None:
        # A typo must never create a long-lived claim for a queue with no
        # worker. Preserve an existing exact snapshot, otherwise expose only
        # the existing sanitized failed-refresh envelope.
        if previous is not None:
            return _decorate_refresh_state(previous, "failed")
        return _decorate_refresh_state(pending_payload, "failed")

    admission_deferred = False
    token = begin_exact_refresh(namespace, normalized_identity)
    if token is not None and not _claim_exact_refresh_admission(
        normalized_identity,
        token,
        lease_seconds=_refresh_dispatch_seconds(),
    ):
        # Capacity is temporary, not a query failure. Release this identity's
        # dispatch claim without persisting a failed state; a later bounded poll
        # can claim the slot after another exact refresh completes.
        finish_exact_refresh(
            namespace,
            normalized_identity,
            token,
            succeeded=True,
        )
        token = None
        admission_deferred = True
    if token is None and state == "running":
        if _release_terminal_dispatch_claim(namespace, normalized_identity):
            token = begin_exact_refresh(namespace, normalized_identity)
            if token is not None and not _claim_exact_refresh_admission(
                normalized_identity,
                token,
                lease_seconds=_refresh_dispatch_seconds(),
            ):
                finish_exact_refresh(
                    namespace,
                    normalized_identity,
                    token,
                    succeeded=True,
                )
                token = None
                admission_deferred = True
    refresh_enqueued = False
    if token is not None:
        refresh_enqueued = _enqueue_exact_refresh(
            namespace,
            normalized_identity,
            token,
            task_queue,
        )

    # Eager test execution (or an exceptionally fast worker) may have already
    # published before enqueue returned. Re-read once; production requests do
    # not wait or poll here.
    current = read_servable(normalized_identity)
    current_state = exact_refresh_state(namespace, normalized_identity)
    if current is not None:
        return _decorate_refresh_state(current, current_state)
    if previous is not None:
        fallback_state = current_state
        if fallback_state is None and (refresh_enqueued or admission_deferred):
            fallback_state = "running"
        return _decorate_refresh_state(previous, fallback_state)
    # ``token is None`` is ambiguous: another request may own a healthy claim,
    # or the cache itself may be unavailable.  Trust a persisted running state,
    # and trust the request that successfully enqueued this refresh.  With
    # neither proof, fail closed instead of showing an endless "preparing"
    # state for work that was never queued.
    terminal_state = current_state
    if terminal_state is None:
        terminal_state = (
            "running" if refresh_enqueued or admission_deferred else "failed"
        )
    return _decorate_refresh_state(pending_payload, terminal_state)


__all__ = [
    "EXACT_AGGREGATION_ACTIVITY_TIMEOUT_SECONDS",
    "EXACT_AGGREGATION_SCHEDULE_TO_START_TIMEOUT_SECONDS",
    "EXACT_AGGREGATION_WORKFLOW_EXECUTION_TIMEOUT_SECONDS",
    "EXACT_AGGREGATION_WORKFLOW_RUN_TIMEOUT_SECONDS",
    "activate_exact_refresh",
    "begin_exact_refresh",
    "exact_refresh_state",
    "exact_payload_is_complete",
    "finish_exact_refresh",
    "mark_refresh_failed",
    "normalize_exact_observe_identity",
    "normalized_snapshot_identity",
    "publish_exact_snapshot",
    "publish_exact_snapshot_for_refresh",
    "record_exact_refresh_dispatch",
    "refresh_claim_is_current",
    "read_or_schedule_exact_snapshot",
    "raw_observe_identity_key",
    "read_exact_snapshot",
    "refresh_failure_seconds",
    "snapshot_cache_key",
]
