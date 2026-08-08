"""Atomic cache boundary for exact analytics snapshots.

The cache is deliberately a result cache, not a work queue.  A caller either
publishes one fully-computed exact payload with ``cache.set`` (an atomic Redis
replacement), or leaves the previous payload untouched.  Partial, sampled,
and degraded responses are rejected at this boundary.
"""

from __future__ import annotations

import hashlib
import json
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

_CACHE_VERSION = 1
_DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60
_DEFAULT_REFRESH_LOCK_SECONDS = 60 * 60
# A refresh claim starts as a short dispatch lease.  The activity promotes it
# to the long running lease before it touches ClickHouse.  This matters during
# rolling deploys: an older Temporal worker can accept the generic workflow but
# reject a newly-registered activity type before our activity function runs.
# Such a terminal workflow failure has no ``finally`` block in this process, so
# a one-hour pre-start claim would otherwise leave every poll falsely pending.
_DEFAULT_REFRESH_DISPATCH_SECONDS = 10 * 60
_DEFAULT_REFRESH_RECONCILE_SECONDS = 5
_DEFAULT_REFRESH_STATUS_TIMEOUT_SECONDS = 0.5
_DEFAULT_REFRESH_FAILURE_SECONDS = 5 * 60
_CACHE_FENCE_FALLBACK_LOCK = RLock()

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


def _refresh_lock_key(namespace: str, identity: Any) -> str:
    return f"{snapshot_cache_key(namespace, identity)}:refresh-lock"


def _refresh_state_key(namespace: str, identity: Any) -> str:
    return f"{snapshot_cache_key(namespace, identity)}:refresh-state"


def _refresh_reconcile_key(namespace: str, identity: Any) -> str:
    return f"{snapshot_cache_key(namespace, identity)}:refresh-reconcile"


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
        60,
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
        5,
        min(
            _refresh_lock_seconds(),
            int(
                getattr(
                    settings,
                    "EXACT_AGGREGATION_REFRESH_DISPATCH_SECONDS",
                    _DEFAULT_REFRESH_DISPATCH_SECONDS,
                )
            ),
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


def begin_exact_refresh(namespace: str, identity: Any) -> str | None:
    """Atomically claim the short pre-activity dispatch lease for a query.

    The worker must call :func:`activate_exact_refresh` before doing any work.
    If no compatible Temporal worker starts the activity, both keys expire and
    the next ordinary poll can safely enqueue a fresh, uniquely fenced claim.
    """

    token = uuid4().hex
    dispatch_seconds = _refresh_dispatch_seconds()
    try:
        claimed = cache.add(
            _refresh_lock_key(namespace, identity),
            token,
            timeout=dispatch_seconds,
        )
        if not claimed:
            return None
        cache.set(
            _refresh_state_key(namespace, identity),
            {"status": "running", "token": token, "phase": "dispatch"},
            timeout=dispatch_seconds,
        )
        return token
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
                return True

        raw_client = redis_client.get_client(write=True)
        return bool(
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

    normalized_identity = normalized_snapshot_identity(identity)
    previous = read_exact_snapshot(namespace, normalized_identity)
    state = exact_refresh_state(namespace, normalized_identity)
    if previous is not None and not refresh:
        return _decorate_refresh_state(previous, state)
    if previous is None and state == "failed" and not refresh:
        return _decorate_refresh_state(pending_payload, state)

    token = begin_exact_refresh(namespace, normalized_identity)
    if token is None and state == "running":
        if _release_terminal_dispatch_claim(namespace, normalized_identity):
            token = begin_exact_refresh(namespace, normalized_identity)
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
                queue="tasks_xl",
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
    # ``token is None`` is ambiguous: another request may own a healthy claim,
    # or the cache itself may be unavailable.  Trust a persisted running state,
    # and trust the request that successfully enqueued this refresh.  With
    # neither proof, fail closed instead of showing an endless "preparing"
    # state for work that was never queued.
    terminal_state = current_state
    if terminal_state is None:
        terminal_state = "running" if refresh_enqueued else "failed"
    return _decorate_refresh_state(pending_payload, terminal_state)


__all__ = [
    "activate_exact_refresh",
    "begin_exact_refresh",
    "exact_refresh_state",
    "exact_payload_is_complete",
    "finish_exact_refresh",
    "mark_refresh_failed",
    "normalized_snapshot_identity",
    "publish_exact_snapshot",
    "publish_exact_snapshot_for_refresh",
    "record_exact_refresh_dispatch",
    "refresh_claim_is_current",
    "read_or_schedule_exact_snapshot",
    "read_exact_snapshot",
    "snapshot_cache_key",
]
