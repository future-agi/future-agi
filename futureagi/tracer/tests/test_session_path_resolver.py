"""
Tests for ``_resolve_session_path`` trace ordering.

Pins the regression where ``traces.<n>.<...>`` ordered traces by
``(created_at, id)``. Voice/agent SDKs stamp every trace in a run with
the same ``created_at``, so the id alphabetical tie-break would pick a
"trace 0" the user never sees at the top of the trace-listing UI --
producing ``Required attribute ... not found on session`` errors when
the path expected an LLM span but got whatever id sorted first.

The resolver now matches ``list_traces_of_session`` (tracer/views/trace.py)
by ordering on the earliest root span's ``start_time``, falling back to
``created_at``.
"""

from datetime import timedelta

import pytest

# Cycle-breaker -- same rationale as ``test_eval_task_runtime``.
import model_hub.tasks  # noqa: F401, E402

from django.utils import timezone  # noqa: E402

from tracer.models.observation_span import ObservationSpan  # noqa: E402
from tracer.models.trace import Trace  # noqa: E402
from tracer.models.trace_session import TraceSession  # noqa: E402
from tracer.utils.eval import _MISSING, _resolve_session_path  # noqa: E402


@pytest.mark.integration
@pytest.mark.django_db
class TestResolveSessionPathTraceOrdering:
    """Trace-collection ordering inside ``_resolve_session_path``."""

    def _make_session_with_two_traces(self, observe_project, *, ids_alpha_first_root_late):
        """Build a session with two traces sharing ``created_at``.

        Returns ``(session, alpha_trace, beta_trace, alpha_root_start,
        beta_root_start)`` where ``alpha_trace.id`` sorts alphabetically
        before ``beta_trace.id``. When ``ids_alpha_first_root_late`` is
        ``True`` (the regression scenario), the alphabetically-first
        trace's root span starts AFTER the other trace's root span -- so
        only the new resolver picks the chronologically-first trace as
        ``traces.0``.
        """
        # Force shared created_at by writing it explicitly. ``auto_now_add``
        # would otherwise stamp each row at the actual insertion instant.
        shared_ts = timezone.now()

        session = TraceSession.objects.create(
            project=observe_project,
            name="ordering-session",
            bookmarked=False,
        )

        # UUIDs are auto-generated; we don't get to pick them, so we
        # create both traces, sort by id, and assign labels accordingly.
        t1 = Trace.objects.create(project=observe_project, session=session, input={"v": "t1"})
        t2 = Trace.objects.create(project=observe_project, session=session, input={"v": "t2"})
        Trace.objects.filter(id__in=[t1.id, t2.id]).update(created_at=shared_ts)
        t1.refresh_from_db()
        t2.refresh_from_db()

        alpha, beta = sorted([t1, t2], key=lambda t: str(t.id))

        # Pick start_times so the alphabetically-first trace's root is
        # later when the regression scenario is requested.
        if ids_alpha_first_root_late:
            alpha_start = shared_ts + timedelta(seconds=5)
            beta_start = shared_ts + timedelta(seconds=1)
        else:
            alpha_start = shared_ts + timedelta(seconds=1)
            beta_start = shared_ts + timedelta(seconds=5)

        ObservationSpan.objects.create(
            id="root_alpha",
            project=observe_project,
            trace=alpha,
            parent_span_id=None,
            name="root_alpha",
            observation_type="llm",
            start_time=alpha_start,
            end_time=alpha_start + timedelta(seconds=1),
            span_attributes={"marker": "alpha"},
        )
        ObservationSpan.objects.create(
            id="root_beta",
            project=observe_project,
            trace=beta,
            parent_span_id=None,
            name="root_beta",
            observation_type="llm",
            start_time=beta_start,
            end_time=beta_start + timedelta(seconds=1),
            span_attributes={"marker": "beta"},
        )

        return session, alpha, beta, alpha_start, beta_start

    def test_traces_0_is_earliest_root_span_when_created_at_ties(
        self, observe_project
    ):
        """When created_at is identical, the trace whose root span starts
        first is ``traces.0`` -- not the alphabetically-first id."""
        session, alpha, beta, _, _ = self._make_session_with_two_traces(
            observe_project, ids_alpha_first_root_late=True
        )

        resolved = _resolve_session_path(session, "traces.0.input")

        # beta's root started 4s before alpha's, so beta is traces.0.
        assert resolved is not _MISSING
        assert resolved == beta.input
        assert resolved != alpha.input

    def test_traces_1_is_later_root_span(self, observe_project):
        """Symmetry check: the alphabetically-first / chronologically-late
        trace is ``traces.1``."""
        session, alpha, _, _, _ = self._make_session_with_two_traces(
            observe_project, ids_alpha_first_root_late=True
        )

        resolved = _resolve_session_path(session, "traces.1.input")

        assert resolved is not _MISSING
        assert resolved == alpha.input

    def test_resolves_span_attribute_through_traces_0(self, observe_project):
        """End-to-end path: ``traces.0.spans.0.span_attributes.marker``
        bottoms out on the chronologically-first trace's root span,
        confirming the ordering propagates through the full chain."""
        session, _, beta, _, _ = self._make_session_with_two_traces(
            observe_project, ids_alpha_first_root_late=True
        )

        resolved = _resolve_session_path(
            session, "traces.0.spans.0.span_attributes.marker"
        )

        assert resolved == "beta"

    def test_alphabetical_tie_break_when_root_starts_match(self, observe_project):
        """When both root start_times are identical, ordering falls back
        to id (``order_by("_root_start", "id")``), preserving determinism
        for sessions with truly simultaneous traces."""
        # Both roots start at the same instant -- id wins the tie-break.
        session, alpha, beta, _, _ = self._make_session_with_two_traces(
            observe_project, ids_alpha_first_root_late=False
        )
        # Override beta's root to start exactly when alpha's does.
        ObservationSpan.objects.filter(id="root_beta").update(
            start_time=ObservationSpan.objects.get(id="root_alpha").start_time
        )

        resolved = _resolve_session_path(session, "traces.0.input")

        assert resolved == alpha.input

    def test_falls_back_to_created_at_when_no_root_span(self, observe_project):
        """A trace with no root span yet (ingestion in flight) uses
        ``created_at`` for ordering -- mirrors the UI's COALESCE."""
        session = TraceSession.objects.create(
            project=observe_project, name="root-less-session", bookmarked=False
        )
        # First trace: created earlier, no spans at all.
        early = Trace.objects.create(
            project=observe_project, session=session, input={"v": "early"}
        )
        Trace.objects.filter(id=early.id).update(
            created_at=timezone.now() - timedelta(minutes=5)
        )
        # Second trace: created later, but with a root span. The root
        # span's start_time is BEFORE early's created_at -- so under the
        # new ordering, the trace with the root span comes first.
        late = Trace.objects.create(
            project=observe_project, session=session, input={"v": "late"}
        )
        ObservationSpan.objects.create(
            id="root_late",
            project=observe_project,
            trace=late,
            parent_span_id=None,
            name="root_late",
            observation_type="llm",
            start_time=timezone.now() - timedelta(minutes=10),
            end_time=timezone.now() - timedelta(minutes=9),
            span_attributes={},
        )

        resolved = _resolve_session_path(session, "traces.0.input")

        assert resolved == late.input
