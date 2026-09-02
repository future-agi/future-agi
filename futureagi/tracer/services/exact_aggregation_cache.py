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
_CACHE_VERSION = 2
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
        if column_id not in {"created_at", "start_time"}:
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


def _observe_identity_alias_key(namespace: str, identity: Any) -> str | None:
    """Address the frozen window chosen for one stable raw Observe request."""

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
    return f"{snapshot_cache_key(namespace, normalized_identity)}:frozen-identity"


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
    return f"exact-aggregation:v{_CACHE_VERSION}:scope-admission:{digest}"


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


def _claim_exact_refresh_admission(
    identity: Any,
    token: str,
    *,
    lease_seconds: int,
) -> bool:
    """Admit a bounded number of distinct exact jobs per tenant scope."""

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
                    _REDIS_CLAIM_SCOPE_ADMISSION_SCRIPT,
                    1,
                    redis_client.make_key(admission_key),
                    now_ms,
                    token,
                    expiry_ms,
                    ttl_margin_ms,
                    _max_inflight_per_scope(),
                )
            )

        with _CACHE_FENCE_FALLBACK_LOCK:
            raw_members = cache.get(admission_key)
            members = dict(raw_members) if isinstance(raw_members, dict) else {}
            members = {
                member: expiry
                for member, expiry in members.items()
                if isinstance(expiry, int) and expiry > now_ms
            }
            if token not in members and len(members) >= _max_inflight_per_scope():
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
            raw_members = cache.get(admission_key)
            members = dict(raw_members) if isinstance(raw_members, dict) else {}
            members = {
                member: expiry
                for member, expiry in members.items()
                if isinstance(expiry, int) and expiry > now_ms
            }
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


def _refresh_failure_seconds() -> int:
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
                        timeout=_refresh_failure_seconds(),
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
            _refresh_failure_seconds() * 1000,
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


def read_or_schedule_exact_snapshot(
    namespace: str,
    identity: Any,
    *,
    refresh: bool,
    pending_payload: Any,
) -> Any:
    """Serve an exact snapshot immediately and run slow refreshes out of band.

    A cache hit is never replaced by a pending response. A cold miss returns a
    non-chartable pending envelope. Failed cold jobs wait for another explicit
    refresh instead of being resubmitted by every polling request.
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
    previous = read_exact_snapshot(namespace, normalized_identity)
    if previous is None and stale_identity is not None:
        previous = read_exact_snapshot(namespace, stale_identity)
    state = exact_refresh_state(namespace, normalized_identity)
    if previous is not None and not refresh:
        return _decorate_refresh_state(previous, state)
    if previous is None and state == "failed" and not refresh:
        return _decorate_refresh_state(pending_payload, state)

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
        try:
            from temporalio.common import WorkflowIDConflictPolicy
            from tracer.tasks.exact_aggregation import (
                refresh_exact_aggregation_snapshot,
            )

            enqueue_result = refresh_exact_aggregation_snapshot.apply_async(
                kwargs={
                    "namespace": namespace,
                    "identity": normalized_identity,
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
            refresh_enqueued = True
            workflow_id = getattr(enqueue_result, "id", None)
            if isinstance(workflow_id, str):
                record_exact_refresh_dispatch(
                    namespace,
                    normalized_identity,
                    token,
                    workflow_id,
                )
        except Exception:
            logger.warning(
                "exact_aggregation_refresh_enqueue_failed",
                namespace=namespace,
                exc_info=True,
            )
            finish_exact_refresh(
                namespace,
                normalized_identity,
                token,
                succeeded=False,
            )

    # Eager test execution (or an exceptionally fast worker) may have already
    # published before enqueue returned. Re-read once; production requests do
    # not wait or poll here.
    current = read_exact_snapshot(namespace, normalized_identity)
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
    "read_exact_snapshot",
    "snapshot_cache_key",
]
