"""Tests for the trace + session derived-field resolver branches.

Pins aggregate formulas, the ``status`` precedence rule, and per-instance
memoisation. Mirrors ``list_traces`` / ``list_sessions``.
"""

import pytest

# Cycle-breaker — same rationale as the other resolver tests.
import model_hub.tasks  # noqa: F401, E402

from datetime import timedelta  # noqa: E402

from django.utils import timezone  # noqa: E402

from tracer.models.observation_span import ObservationSpan  # noqa: E402
from tracer.models.trace import Trace  # noqa: E402
from tracer.models.trace_session import TraceSession  # noqa: E402
from tracer.utils.eval import (  # noqa: E402
    _SESSION_DERIVED_FIELDS,
    _TRACE_DERIVED_FIELDS,
    _compute_session_derived,
    _compute_trace_derived,
    _resolve_session_path,
    _resolve_trace_path,
)


def _make_span(trace, project, *, sid, parent=None, **fields):
    defaults = dict(
        id=sid,
        project=project,
        trace=trace,
        parent_span_id=parent,
        name=f"span-{sid}",
        observation_type=fields.pop("observation_type", "llm"),
        start_time=fields.pop("start_time", None),
        end_time=fields.pop("end_time", None),
        latency_ms=fields.pop("latency_ms", None),
        prompt_tokens=fields.pop("prompt_tokens", 0),
        completion_tokens=fields.pop("completion_tokens", 0),
        total_tokens=fields.pop("total_tokens", 0),
        cost=fields.pop("cost", 0.0),
        status=fields.pop("status", "OK"),
        input=fields.pop("input", None),
    )
    defaults.update(fields)
    return ObservationSpan.objects.create(**defaults)


@pytest.mark.integration
@pytest.mark.django_db
class TestTraceDerivedFields:
    """Aggregate formulas from ``tracer/views/trace.py``."""

    def test_aggregates_match_summary_reduction(self, observe_project):
        trace = Trace.objects.create(project=observe_project)
        now = timezone.now()
        _make_span(
            trace,
            observe_project,
            sid="t-root",
            parent=None,
            observation_type="chain",
            name="root-chain",
            start_time=now,
            latency_ms=1500,
            prompt_tokens=100,
            completion_tokens=200,
            total_tokens=300,
            cost=0.0012,
            status="OK",
            input={"messages": [{"role": "user", "content": "hi"}]},
        )
        # Child error — bumps error_count but not status (root-only).
        _make_span(
            trace,
            observe_project,
            sid="t-child-1",
            parent="t-root",
            prompt_tokens=50,
            completion_tokens=70,
            total_tokens=120,
            cost=0.0008,
            status="ERROR",
        )

        derived = _compute_trace_derived(trace)
        assert derived["node_type"] == "chain"
        assert derived["trace_name"] == "root-chain"
        assert derived["start_time"] == now
        assert derived["status"] == "OK"
        assert derived["total_tokens"] == 420
        assert derived["total_prompt_tokens"] == 150
        assert derived["total_completion_tokens"] == 270
        assert derived["total_cost"] == round(0.0012 + 0.0008, 6)
        # Root spans only.
        assert derived["total_duration_ms"] == 1500
        assert derived["total_spans"] == 2
        assert derived["error_count"] == 1

    def test_status_promotes_to_error_when_root_errors(self, observe_project):
        trace = Trace.objects.create(project=observe_project)
        _make_span(
            trace, observe_project, sid="r1", parent=None, status="ERROR"
        )
        _make_span(
            trace, observe_project, sid="r2", parent=None, status="OK"
        )
        assert _compute_trace_derived(trace)["status"] == "ERROR"

    def test_status_unset_with_no_root_status(self, observe_project):
        trace = Trace.objects.create(project=observe_project)
        _make_span(
            trace, observe_project, sid="r1", parent=None, status="UNSET"
        )
        assert _compute_trace_derived(trace)["status"] == "UNSET"

    def test_node_type_falls_back_when_no_root(self, observe_project):
        """No root span -> ``unknown`` / ``[ Incomplete Trace ]`` /
        Trace.created_at."""
        trace = Trace.objects.create(project=observe_project)
        derived = _compute_trace_derived(trace)
        assert derived["node_type"] == "unknown"
        assert derived["trace_name"] == "[ Incomplete Trace ]"
        assert derived["start_time"] == trace.created_at

    def test_resolver_routes_through_derived_branch(self, observe_project):
        trace = Trace.objects.create(project=observe_project)
        _make_span(
            trace,
            observe_project,
            sid="r1",
            parent=None,
            total_tokens=42,
            cost=0.005,
        )
        assert _resolve_trace_path(trace, "total_tokens") == 42
        assert _resolve_trace_path(trace, "total_cost") == 0.005

    def test_per_instance_cache_avoids_duplicate_queries(
        self, observe_project, django_assert_num_queries
    ):
        trace = Trace.objects.create(project=observe_project)
        _make_span(trace, observe_project, sid="r1", parent=None, total_tokens=10)

        _compute_trace_derived(trace)  # warm
        with django_assert_num_queries(0):
            _compute_trace_derived(trace)
            _compute_trace_derived(trace)


@pytest.mark.integration
@pytest.mark.django_db
class TestSessionDerivedFields:
    """Aggregate formulas from ``tracer/views/trace_session.py``."""

    def test_aggregates_across_session_spans(self, observe_project):
        session = TraceSession.objects.create(
            project=observe_project, name="s"
        )
        t0 = Trace.objects.create(project=observe_project, session=session)
        t1 = Trace.objects.create(project=observe_project, session=session)
        start_ts = timezone.now()
        _make_span(
            t0,
            observe_project,
            sid="s0",
            parent=None,
            start_time=start_ts,
            end_time=start_ts + timedelta(seconds=1),
            total_tokens=10,
            prompt_tokens=4,
            completion_tokens=6,
            cost=0.001,
            input={"first": True},
        )
        _make_span(
            t1,
            observe_project,
            sid="s1",
            parent=None,
            start_time=start_ts + timedelta(seconds=5),
            end_time=start_ts + timedelta(seconds=10),
            total_tokens=20,
            prompt_tokens=8,
            completion_tokens=12,
            cost=0.003,
            input={"last": True},
        )

        derived = _compute_session_derived(session)
        assert derived["total_tokens"] == 30
        assert derived["total_prompt_tokens"] == 12
        assert derived["total_completion_tokens"] == 18
        assert derived["total_cost"] == round(0.001 + 0.003, 6)
        assert derived["start_time"] == start_ts
        assert derived["end_time"] == start_ts + timedelta(seconds=10)
        assert derived["duration"] == 10.0
        assert derived["total_traces"] == 2
        assert derived["first_message"] == {"first": True}
        assert derived["last_message"] == {"last": True}

    def test_empty_session_returns_zeros_not_nulls(self, observe_project):
        """Coalesce parity with list_sessions."""
        session = TraceSession.objects.create(
            project=observe_project, name="empty"
        )
        derived = _compute_session_derived(session)
        assert derived["total_tokens"] == 0
        assert derived["total_cost"] == 0.0
        assert derived["total_traces"] == 0
        assert derived["duration"] == 0
        assert derived["first_message"] is None
        assert derived["last_message"] is None

    def test_resolver_routes_through_derived_branch(self, observe_project):
        session = TraceSession.objects.create(
            project=observe_project, name="rs"
        )
        trace = Trace.objects.create(project=observe_project, session=session)
        _make_span(
            trace, observe_project, sid="x", parent=None, total_tokens=77
        )
        assert _resolve_session_path(session, "total_tokens") == 77

    def test_derived_fields_whitelist_pins_the_set(self):
        """Tripwire when the sets change; picker tests need updating too."""
        assert _TRACE_DERIVED_FIELDS == frozenset(
            {
                "node_type",
                "trace_name",
                "start_time",
                "status",
                "total_tokens",
                "total_prompt_tokens",
                "total_completion_tokens",
                "total_cost",
                "total_duration_ms",
                "total_spans",
                "error_count",
            }
        )
        assert _SESSION_DERIVED_FIELDS == frozenset(
            {
                "total_tokens",
                "total_prompt_tokens",
                "total_completion_tokens",
                "total_cost",
                "start_time",
                "end_time",
                "duration",
                "total_traces",
                "first_message",
                "last_message",
            }
        )
