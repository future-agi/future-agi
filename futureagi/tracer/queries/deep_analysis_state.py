"""Transient per-trace deep-analysis job state.

The Error Feed's on-demand "Run Deep Analysis" used to track its per-trace job
state in ``Trace.error_analysis_status`` (a PG column on ``tracer_trace``).
Post CH-cutover that table is gone, so the column can't be read or written.

The only genuinely *transient* state is "running": a finished run is recorded by
a ``TraceErrorAnalysis`` row (feed-owned PG, survives the drop) which is the
source of truth for "done". So the marker lives in the cache, not a table:

- ``set_running`` is an atomic ``cache.add`` — two rapid dispatches collapse to
  one (double-click guard), and a crashed worker self-heals when the marker
  TTLs out. The old column left such traces stuck in PROCESSING forever.
- "done" is never stored here — it derives from ``TraceErrorAnalysis`` existence.
"""

from django.core.cache import cache

# Ceiling matches the analysis activity's ``time_limit``; a marker that outlives
# a crashed run TTLs back to "idle" so the "Run Deep Analysis" button re-enables.
_TTL_SECONDS = 3600

_RUNNING = "running"
_FAILED = "failed"


def _key(trace_id: str) -> str:
    return f"deep_analysis:state:{trace_id}"


def set_running(trace_id: str) -> bool:
    """Claim the trace as running. Returns ``True`` if this call set the marker,
    ``False`` if a run was already in flight — an atomic double-click guard."""
    return bool(cache.add(_key(trace_id), _RUNNING, _TTL_SECONDS))


def is_running(trace_id: str) -> bool:
    return cache.get(_key(trace_id)) == _RUNNING


def set_failed(trace_id: str) -> None:
    """Record a failed run so the UI can surface it until the marker TTLs out."""
    cache.set(_key(trace_id), _FAILED, _TTL_SECONDS)


def clear(trace_id: str) -> None:
    cache.delete(_key(trace_id))


def status(trace_id: str, *, has_analysis: bool) -> str:
    """Frontend job state: ``done`` | ``running`` | ``failed`` | ``idle``.

    A completed ``TraceErrorAnalysis`` row wins ("done"). Otherwise the transient
    marker ("running"/"failed"), else "idle". Mirrors the old
    status→feed-state map without touching the dropped column.
    """
    if has_analysis:
        return "done"
    marker = cache.get(_key(trace_id))
    if marker in (_RUNNING, _FAILED):
        return marker
    return "idle"
