"""Repro + regression harness for the Error Feed after the legacy tracer PG
tables are dropped.

Traces now live only in ClickHouse (fi-collector -> CH); the legacy
``tracer_trace`` / ``tracer_observation_span`` / ``trace_session`` /
``tracer_enduser`` PG tables are being dropped org-wide. Every FK from the
feed's own tables to those models is ``db_constraint=False`` so the raw
``*_id`` columns survive the drop -- but any ORM JOIN (``select_related``,
``trace__session`` traversal) or ``Trace.objects.*`` read still targets the
missing relation and raises ``ProgrammingError`` (42P01), which also poisons
the request transaction.

These tests seed a cluster + its trace **in ClickHouse only**, drop the four
tracer PG tables inside the test transaction (Postgres DDL is transactional,
so it rolls back at teardown), then exercise every feed read path. Before the
CH-native conversion each path raises 42P01; after it, each returns CH-sourced
data.

One drop+call per test: the first 42P01 aborts the transaction, so isolating
each path keeps a real 42P01 from masquerading as "transaction is aborted" on
the paths that follow.
"""

import uuid
from datetime import timedelta

import pytest
from django.db import ProgrammingError, connection
from django.utils import timezone

from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace
from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    FeedIssueStatus,
    Priority,
    TraceErrorGroup,
)
from tracer.tests._ch_seed import seed_ch_span
from tracer.utils import feed as feed_service

# The tracer-owned PG tables being dropped. Order/‑CASCADE handles any
# leftover dependent objects; all FKs into them are already db_constraint=False.
_DROPPED_TABLES = (
    "tracer_observation_span",
    "tracer_trace",
    "trace_session",
    "tracer_enduser",
)


def _drop_tracer_pg_tables():
    """Simulate the org-wide drop inside the test transaction."""
    with connection.cursor() as cursor:
        cursor.execute(
            "DROP TABLE IF EXISTS "
            + ", ".join(_DROPPED_TABLES)
            + " CASCADE"
        )


def _seed_ch_root(project, trace_id, *, status="OK", parent="", input_=None, output=None):
    """Seed ONE CH root span for ``trace_id`` (no PG Trace row).

    The span carries an UNSAVED ``Trace`` so ``seed_ch_span`` reads its FK from
    the instance cache instead of hitting PG for a trace that lives only in CH.
    """
    span = ObservationSpan(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=Trace(id=trace_id, project=project),
        parent_span_id=parent,
        name="root",
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=5),
        end_time=timezone.now(),
        input=input_ if input_ is not None else {"prompt": "hi"},
        output=output if output is not None else {"response": "hello"},
        model="gpt-4",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        latency_ms=500,
        status=status,
    )
    seed_ch_span(span)
    return span


@pytest.fixture
def seeded_cluster(db, project):
    """A scanner cluster + one CH-only member trace + a CH-only success trace.

    Returns ``(cluster, trace_id, success_trace_id)``. Nothing is written to the
    tracer PG tables — the trace exists purely in ClickHouse.
    """
    now = timezone.now()
    trace_id = str(uuid.uuid4())
    success_trace_id = str(uuid.uuid4())

    cluster = TraceErrorGroup.objects.create(
        project=project,
        cluster_id="K-DROP-1",
        error_type="drop-error",
        source=ClusterSource.SCANNER,
        issue_group="Tool Failures",
        issue_category="Language-only",
        fix_layer="Tools",
        title="drop repro issue",
        status=FeedIssueStatus.ESCALATING,
        priority=Priority.MEDIUM,
        first_seen=now,
        last_seen=now,
        error_count=1,
        unique_traces=1,
        success_trace_id=success_trace_id,
    )
    ErrorClusterTraces.objects.create(cluster=cluster, trace_id=trace_id)

    _seed_ch_root(project, trace_id, input_={"prompt": "fail"}, output={"response": "bad"})
    _seed_ch_root(
        project, success_trace_id, input_={"prompt": "ok"}, output={"response": "good"}
    )
    return cluster, trace_id, success_trace_id


@pytest.mark.integration
@pytest.mark.django_db
class TestFeedSurvivesTracerPgDrop:
    """Every feed read path must resolve trace data from CH, never PG."""

    def _pids(self, cluster):
        return [str(cluster.project_id)]

    def test_list_clusters(self, seeded_cluster):
        cluster, _, _ = seeded_cluster
        _drop_tracer_pg_tables()
        resp = feed_service.list_feed_issues(self._pids(cluster))
        assert resp.total == 1
        assert resp.data[0].cluster_id == "K-DROP-1"

    def test_stats(self, seeded_cluster):
        cluster, _, _ = seeded_cluster
        _drop_tracer_pg_tables()
        stats = feed_service.get_feed_stats(self._pids(cluster))
        assert stats is not None

    def test_cluster_detail(self, seeded_cluster):
        cluster, trace_id, success_trace_id = seeded_cluster
        _drop_tracer_pg_tables()
        detail = feed_service.get_feed_detail("K-DROP-1", self._pids(cluster))
        assert detail is not None
        # success + representative previews must hydrate from the CH root span.
        assert detail.success_trace is not None
        assert detail.success_trace.trace_id == success_trace_id

    def test_overview(self, seeded_cluster):
        cluster, _, _ = seeded_cluster
        _drop_tracer_pg_tables()
        # The pattern-summary baseline hits the agentic_eval vector store (a
        # separate CH on :9000, unaffected by the tracer PG drop and absent from
        # the test stack); stub it so this test isolates the PG-drop paths.
        from unittest.mock import patch

        with patch("tracer.queries.feed._passing_baseline_trace_ids", return_value=[]):
            overview = feed_service.get_overview_tab("K-DROP-1", self._pids(cluster))
        assert overview is not None
        assert overview.representative_total >= 1

    def test_traces_tab(self, seeded_cluster):
        cluster, trace_id, _ = seeded_cluster
        _drop_tracer_pg_tables()
        traces = feed_service.get_traces_tab("K-DROP-1", self._pids(cluster))
        assert traces is not None

    def test_trends_tab(self, seeded_cluster):
        cluster, _, _ = seeded_cluster
        _drop_tracer_pg_tables()
        trends = feed_service.get_trends_tab("K-DROP-1", self._pids(cluster))
        assert trends is not None

    def test_sidebar(self, seeded_cluster):
        cluster, trace_id, _ = seeded_cluster
        _drop_tracer_pg_tables()
        sidebar = feed_service.get_sidebar(
            "K-DROP-1", self._pids(cluster), trace_id=trace_id
        )
        assert sidebar is not None

    def test_deep_analysis_get(self, seeded_cluster):
        cluster, trace_id, _ = seeded_cluster
        _drop_tracer_pg_tables()
        resp = feed_service.get_deep_analysis(
            "K-DROP-1", self._pids(cluster), trace_id=trace_id
        )
        # No analysis rows seeded -> status is a non-error "idle"/"pending".
        assert resp is not None

    def test_deep_analysis_dispatch(self, seeded_cluster):
        cluster, trace_id, _ = seeded_cluster
        _drop_tracer_pg_tables()
        from unittest.mock import patch

        # Lazily imported inside dispatch_deep_analysis, so patch it at its home.
        with patch("tracer.tasks.run_deep_analysis_on_demand") as task:
            resp = feed_service.dispatch_deep_analysis(
                "K-DROP-1", self._pids(cluster), trace_id=trace_id
            )
        assert resp is not None
        assert resp.status == "running"
        assert task.delay.called


class TestDeepAnalysisState:
    """The transient per-trace marker that replaces Trace.error_analysis_status."""

    def setup_method(self):
        from django.core.cache import cache

        cache.clear()

    def test_double_click_guard_and_lifecycle(self):
        from tracer.queries import deep_analysis_state as das

        tid = str(uuid.uuid4())
        # Nothing set → idle; no completed analysis.
        assert das.status(tid, has_analysis=False) == "idle"
        # First claim wins, a rapid second claim loses (single dispatch).
        assert das.set_running(tid) is True
        assert das.set_running(tid) is False
        assert das.status(tid, has_analysis=False) == "running"
        # A completed analysis row always wins over the marker.
        assert das.status(tid, has_analysis=True) == "done"
        # Failure is surfaced until it TTLs out.
        das.set_failed(tid)
        assert das.status(tid, has_analysis=False) == "failed"
        # Clearing (worker success) drops back to idle when no analysis exists.
        das.clear(tid)
        assert das.status(tid, has_analysis=False) == "idle"


@pytest.mark.integration
@pytest.mark.django_db
def test_new_ch_reader_methods_smoke(project):
    """Direct smoke of the CH reader methods the feed CH-native swap added/changed
    — the SQL must compile and run against real ClickHouse (these paths aren't
    all hit by the feed-endpoint tests above)."""
    from tracer.services.clickhouse.v2 import get_reader

    tid = str(uuid.uuid4())
    _seed_ch_root(project, tid, input_={"prompt": "hi"})

    with get_reader() as reader:
        # trace_id → trace_session_id (root has no session → None, no crash)
        sessions = reader.trace_session_ids_by_trace_ids([tid])
        assert tid in sessions

        # roots_only window count = trace count (one root per trace), not spans
        trace_count = reader.count_with_filters(
            project_id=str(project.id), roots_only=True
        )
        assert trace_count >= 1

        # distinct trace_ids for the project, newest first
        recent = reader.recent_root_trace_ids_by_project(str(project.id), limit=10)
        assert tid in recent


@pytest.mark.integration
@pytest.mark.django_db
def test_drop_actually_removes_table(project):
    """Guard: the drop truly removes the relation (so the read-path tests are
    a real 42P01 repro, not a no-op), and it rolls back cleanly afterwards."""
    _drop_tracer_pg_tables()
    with pytest.raises(ProgrammingError):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM tracer_trace LIMIT 1")
