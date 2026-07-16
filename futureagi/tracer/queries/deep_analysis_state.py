"""Transient per-trace deep-analysis job state.

The Error Feed's on-demand "Run Deep Analysis" used to track its per-trace job
state in ``Trace.error_analysis_status`` (a PG column on ``tracer_trace``).
Post CH-cutover that table is gone, so the column can't be read or written.

The only genuinely *transient* state is "running" / "failed": a finished run is
recorded by a ``TraceErrorAnalysis`` row (feed-owned PG, survives the drop) which
is the source of truth for "done". So the marker lives in the cache, not a table.

Two distinct keys back it so the failed state can never block a retry:

- ``set_running`` is an atomic ``cache.add`` on the RUNNING key — two rapid
  dispatches collapse to one (double-click guard), and a crashed worker
  self-heals when the marker TTLs out (the old column left such traces stuck in
  PROCESSING forever). It first clears any FAILED marker so a retry claims cleanly.
- ``set_failed`` clears the RUNNING key and sets the FAILED key, so the next
  ``set_running`` claim isn't blocked by a stale running marker.
- "done" is never stored here — it derives from ``TraceErrorAnalysis`` existence.
"""

from django.core.cache import cache

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


def set_failed(trace_id: str) -> None:
    """Record a failed run (until it TTLs out) and release the running claim so a
    retry can re-dispatch."""
    cache.delete(_running_key(trace_id))
    cache.set(_failed_key(trace_id), "1", _TTL_SECONDS)


def clear(trace_id: str) -> None:
    cache.delete(_running_key(trace_id))
    cache.delete(_failed_key(trace_id))


def status(trace_id: str, *, has_analysis: bool) -> str:
    """Frontend job state: ``done`` | ``running`` | ``failed`` | ``idle``.

    A completed ``TraceErrorAnalysis`` row wins ("done"). Otherwise the transient
    running/failed marker, else "idle" — mirroring the old status→feed-state map
    without touching the dropped column.
    """
    if has_analysis:
        return "done"
    if cache.get(_running_key(trace_id)) is not None:
        return "running"
    if cache.get(_failed_key(trace_id)) is not None:
        return "failed"
    return "idle"
