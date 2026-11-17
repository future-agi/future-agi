"""Feed state: durable background receipts first, legacy cache/rows otherwise.

The legacy Judge pipeline keeps its cache contract. Background attempts have a
separate PG lifecycle row: neither a partial finding nor cache eviction can
establish completion. Cache mutations never change that durable row.
"""

from django.core.cache import cache

from tracer.queries import background_analysis_state

# Ceiling matches the analysis activity's ``time_limit``; a marker that outlives
# a crashed run TTLs back to "idle" so the "Run Deep Analysis" button re-enables.
_TTL_SECONDS = 3600


def _running_key(trace_id: str) -> str:
    return f"deep_analysis:running:{trace_id}"


def _failed_key(trace_id: str) -> str:
    return f"deep_analysis:failed:{trace_id}"


def set_running(trace_id: str) -> bool:
    """Claim the trace as running. Returns ``True`` if this call set the marker,
    ``False`` if a run was already in flight — an atomic double-click guard. A
    prior failure never blocks the claim (its marker is cleared first)."""
    cache.delete(_failed_key(trace_id))
    return bool(cache.add(_running_key(trace_id), "1", _TTL_SECONDS))


def is_running(trace_id: str) -> bool:
    return cache.get(_running_key(trace_id)) is not None


def set_failed(trace_id: str, *, code: str | None = None) -> None:
    """Record a failed run (until it TTLs out) and release the running claim so a
    retry can re-dispatch."""
    cache.delete(_running_key(trace_id))
    cache.set(_failed_key(trace_id), code or "1", _TTL_SECONDS)


def clear(trace_id: str) -> None:
    cache.delete(_running_key(trace_id))
    cache.delete(_failed_key(trace_id))


def clear_background_running(trace_id: str) -> None:
    """Compare before releasing the existing running marker, never a failure.

    A watchdog can publish an abort while cache.delete is blocked. Success must
    therefore NEVER delete the failed key, even if it was absent at this check.
    This compatibility helper only touches the legacy cache. Background workers
    no longer call it: their generation-fenced receipt is the sole authority.
    """
    if cache.get(_failed_key(trace_id)) is not None:
        return
    if cache.get(_running_key(trace_id)) == "1":
        cache.delete(_running_key(trace_id))


def background_status(row) -> str:
    if row.status == "completed":
        return "done"
    if row.status == "aborted":
        return "failed"
    return "running"


def status(trace_id: str, *, has_analysis: bool, project_ids=None) -> str:
    row = background_analysis_state.get_state(trace_id, project_ids=project_ids)
    if row is not None:
        return background_status(row)
    if _cached_failure_code(trace_id) is not None:
        return "failed"
    if has_analysis:
        return "done"
    if cache.get(_running_key(trace_id)) is not None:
        return "running"
    if cache.get(_failed_key(trace_id)) is not None:
        return "failed"
    return "idle"


def failure_code(trace_id: str, *, project_ids=None) -> str | None:
    """Public typed outcome; do not expose arbitrary exception text."""
    row = background_analysis_state.get_state(trace_id, project_ids=project_ids)
    if row is not None:
        return row.reason_code or None if row.status == "aborted" else None
    return _cached_failure_code(trace_id)


def _cached_failure_code(trace_id):
    code = cache.get(_failed_key(trace_id))
    if code in ("analysis_access_revoked", "analysis_deadline_exceeded"):
        return code
    return None
