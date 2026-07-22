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

CH DEPENDENCY (P3b step2 / Slice D — PG_ORM_READ_MIGRATION). The ``traces``
branch of ``_resolve_session_path`` now derives the session's trace SET from
ClickHouse spans (``span_reader.session_trace_ids``), NOT the dead
``trace_session.traces`` reverse FK (``Trace.session`` is ``None`` post-flip).
So these tests must SEED the CH ``spans`` table with each trace's spans (carrying
``trace_session_id = session.id``) for the session's traces to be discoverable;
the PG ``Trace`` rows (hydration) and PG ``ObservationSpan`` root rows (the
``_root_start`` ORDERING subquery, still PG) stay exactly as before. A trace with
NO spans is — correctly, post-flip — absent from the CH-derived set (a trace
exists in CH only once it has spans), so the root-less case asserts only on the
trace that DOES carry a span. CH is the writable test sidecar (``test_tfc``); the
test refuses to run against the ``ch_rehearsal`` cross-cutover baseline so seeding
can never pollute it, and skips if CH is unreachable.
"""

from datetime import timedelta

import pytest
from django.utils import timezone  # noqa: E402

# Cycle-breaker -- same rationale as ``test_eval_task_runtime``.
import model_hub.tasks  # noqa: F401, E402
from tracer.models.observation_span import ObservationSpan  # noqa: E402
from tracer.models.trace import Trace  # noqa: E402
from tracer.models.trace_session import TraceSession  # noqa: E402
from tracer.tests._ch_seed import seed_ch_spans, truncate_ch_spans  # noqa: E402
from tracer.utils.eval import _MISSING, _resolve_session_path  # noqa: E402


@pytest.fixture(autouse=True)
def _ch_spans_isolation():
    """Per-test CH isolation: refuse the ch_rehearsal baseline (seeding here must
    never touch it), skip if CH is unreachable, and TRUNCATE the shared ``spans``
    table before AND after so each ordering case sees only its own seeded rows."""
    from tracer.services.clickhouse.v2 import get_v2_config

    cfg = get_v2_config()
    if cfg.get("database") == "ch_rehearsal":
        pytest.skip(
            "refusing to seed CH spans against the ch_rehearsal baseline "
            "(set CH25_DATABASE to the writable test db, e.g. test_tfc)"
        )
    try:
        truncate_ch_spans()
    except Exception:
        pytest.skip("ClickHouse not reachable for the session-path-resolver test")
    yield
    try:
        truncate_ch_spans()
    except Exception:
        pass


@pytest.mark.integration
@pytest.mark.django_db
class TestResolveSessionPathTraceOrdering:
    """Trace-collection ordering inside ``_resolve_session_path``."""

    def _make_session_with_two_traces(
        self, observe_project, *, ids_alpha_first_root_late
    ):
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
        t1 = Trace.objects.create(
            project=observe_project, session=session, input={"v": "t1"}
        )
        t2 = Trace.objects.create(
            project=observe_project, session=session, input={"v": "t2"}
        )
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

        # Seed CH so ``session_trace_ids`` (the migrated ``traces`` branch)
        # discovers BOTH traces: seed_ch_spans derives each row's
        # ``trace_session_id`` from ``span.trace.session_id`` (= this session),
        # so both root spans carry the session id. The PG rows above stay for
        # hydration + the PG ``_root_start`` ordering subquery.
        seed_ch_spans(
            ObservationSpan.objects.filter(id__in=["root_alpha", "root_beta"])
        )

        return session, alpha, beta, alpha_start, beta_start

    def test_traces_0_is_earliest_root_span_when_created_at_ties(self, observe_project):
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
        """The ``_root_start`` ordering still COALESCEs to ``created_at`` for a
        trace whose (CH-present) spans are all non-root.

        Post-flip note: the trace SET is CH-derived, so a trace with NO spans at
        all is absent from the session's ``traces`` (a trace exists in CH only once
        it carries a span — the faithful new behaviour). This test therefore gives
        the ordering-relevant trace a CH span and asserts it is ``traces.0``; the
        ``created_at`` COALESCE fallback is still exercised because that span is
        NON-root (no ``parent_span_id IS NULL`` row → the PG ``_root_start``
        subquery yields NULL → COALESCE to ``created_at``)."""
        session = TraceSession.objects.create(
            project=observe_project, name="root-less-session", bookmarked=False
        )
        # First trace: created LATER, only a NON-root span → its _root_start
        # COALESCEs to its (later) created_at.
        early = Trace.objects.create(
            project=observe_project, session=session, input={"v": "early"}
        )
        Trace.objects.filter(id=early.id).update(
            created_at=timezone.now() - timedelta(minutes=1)
        )
        ObservationSpan.objects.create(
            id="child_early",
            project=observe_project,
            trace=early,
            parent_span_id="some_missing_root",  # NON-root → no _root_start
            name="child_early",
            observation_type="llm",
            start_time=timezone.now() - timedelta(minutes=2),
            end_time=timezone.now() - timedelta(minutes=1),
            span_attributes={},
        )
        # Second trace: created EARLIER, with a real root span whose start_time
        # is the earliest -- so it orders as traces.0 ahead of ``early``.
        late = Trace.objects.create(
            project=observe_project, session=session, input={"v": "late"}
        )
        Trace.objects.filter(id=late.id).update(
            created_at=timezone.now() - timedelta(minutes=5)
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

        # Seed CH so BOTH traces are in the session's trace set (each has >=1 span).
        seed_ch_spans(
            ObservationSpan.objects.filter(id__in=["child_early", "root_late"])
        )

        resolved = _resolve_session_path(session, "traces.0.input")

        # ``late`` (root start −10m) orders before ``early`` (no root → created_at
        # −1m). The COALESCE-to-created_at path is what places ``early`` last.
        assert resolved == late.input
