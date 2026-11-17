"""The endpoint state row as :class:`webhooks.core.health.HealthState`, and back (DESIGN.md sections 4.2 and 7.7).

The settle folds each counted attempt into the row under its lock; the health pass reads it to re-check the slow lane
and the 24-hour rule for endpoints that have stopped getting attempts. Times on the row are datetimes; the core works
in epoch milliseconds.
"""

from __future__ import annotations

from webhooks.core.health import HealthState
from webhooks.models import EndpointState
from webhooks.store_rows import dt_of, ms_of


def state_of(st: EndpointState) -> HealthState:
    run = st.consecutive_failed_attempts
    return HealthState(
        consecutive_failed=run,
        consecutive_ssrf=st.consecutive_ssrf_blocked,
        failing_since_ms=ms_of(st.failing_since) if run else None,
        last_failure_ms=ms_of(st.last_failure_at) if run else None,
        last_failure_outcome=(st.last_outcome or None) if run else None,
        latencies_ms=tuple(int(x) for x in (st.recent_latency_ms or ())),
        timeouts_ms=tuple(int(x) for x in (st.recent_timeouts_at or ())),
    )


def write_state(st: EndpointState, hs: HealthState) -> None:
    """Copy ``hs`` onto the row (the caller saves it). ``last_failure_at`` keeps the last failure after a success."""
    st.consecutive_failed_attempts = hs.consecutive_failed
    st.consecutive_ssrf_blocked = hs.consecutive_ssrf
    st.failing_since = (
        dt_of(hs.failing_since_ms) if hs.failing_since_ms is not None else None
    )
    if hs.last_failure_ms is not None:
        st.last_failure_at = dt_of(hs.last_failure_ms)
    st.recent_latency_ms = list(hs.latencies_ms)
    st.recent_timeouts_at = list(hs.timeouts_ms)
