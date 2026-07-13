"""Phase 2 — ``add-items`` endpoint filter-mode tests.

Covers:
  - Backward compat: the existing ``items`` payload still works.
  - Filter-mode: happy path, exclude_ids, duplicates, truncation 400.
  - Validation: both payload forms together, neither present,
    unsupported mode, unsupported source_type.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from model_hub.models.ai_model import AIModel
from model_hub.models.annotation_queues import AnnotationQueue, QueueItem
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project, ProjectSourceChoices
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.tests._ch_seed import seed_ch_span

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _seed_ch_trace_root(trace):
    """Give a bare PG ``trace`` a CH-only root span so the enumerated add path
    resolves it CH-native (tracer sources are read from ClickHouse only). Built in
    memory and seeded to CH — never written to PG (the tracer tables are dropped)."""
    import uuid

    span = ObservationSpan(
        id=f"chroot_{uuid.uuid4().hex[:16]}",
        project=trace.project,
        trace=trace,
        name="trace root",
        observation_type="agent",
        start_time=timezone.now() - timedelta(seconds=1),
        end_time=timezone.now(),
        status="OK",
    )
    seed_ch_span(span)  # CH only — NOT ObservationSpan.objects.create


@pytest.fixture
def observe_project(db, organization, workspace):
    return Project.objects.create(
        name="BulkAdd Observe Project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


@pytest.fixture
def active_queue(db, organization, workspace):
    return AnnotationQueue.objects.create(
        name="Bulk Test Queue",
        organization=organization,
        workspace=workspace,
    )


def _add_items_url(queue_id):
    return f"/model-hub/annotation-queues/{queue_id}/items/add-items/"


def _api_filter(column_id, filter_type, filter_op, filter_value):
    return {
        "column_id": column_id,
        "filter_config": {
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


# --------------------------------------------------------------------------
# Backward compat — existing ``items`` payload
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestAddItemsEnumeratedRegression:
    def test_enumerated_happy_path(self, auth_client, active_queue, observe_project):
        t = Trace.objects.create(project=observe_project, name="t1")
        _seed_ch_trace_root(t)
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {"items": [{"source_type": "trace", "source_id": str(t.id)}]},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 1
        assert result["duplicates"] == 0
        assert result["errors"] == []

    def test_enumerated_duplicate_detection(
        self, auth_client, active_queue, observe_project
    ):
        t = Trace.objects.create(project=observe_project, name="t-dup")
        _seed_ch_trace_root(t)
        payload = {"items": [{"source_type": "trace", "source_id": str(t.id)}]}
        auth_client.post(_add_items_url(active_queue.id), payload, format="json")
        resp = auth_client.post(_add_items_url(active_queue.id), payload, format="json")
        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 0
        assert result["duplicates"] == 1


# --------------------------------------------------------------------------
# Filter-mode — happy + exclude + duplicates + truncation
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestAddItemsFilterMode:
    def test_filter_mode_no_filter_adds_all_project_traces(
        self, auth_client, active_queue, observe_project
    ):
        for i in range(3):
            Trace.objects.create(project=observe_project, name=f"t-{i}")

        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 3
        assert result["duplicates"] == 0
        assert result["errors"] == []
        assert result["total_matching"] == 3

    def test_filter_mode_respects_exclude_ids(
        self, auth_client, active_queue, observe_project
    ):
        traces = [
            Trace.objects.create(project=observe_project, name=f"t-{i}")
            for i in range(5)
        ]
        exclude = [str(traces[0].id), str(traces[1].id)]

        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                    "exclude_ids": exclude,
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 3
        assert result["total_matching"] == 3

    def test_filter_mode_trace_id_filter_adds_exact_trace(
        self, auth_client, active_queue, observe_project
    ):
        target = Trace.objects.create(project=observe_project, name="target-trace")
        other = Trace.objects.create(project=observe_project, name="other-trace")

        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                    "filter": [
                        _api_filter("trace_id", "text", "equals", str(target.id))
                    ],
                }
            },
            format="json",
        )

        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 1
        assert result["total_matching"] == 1
        assert QueueItem.objects.filter(trace=target, deleted=False).exists()
        assert not QueueItem.objects.filter(trace=other, deleted=False).exists()

    def test_filter_mode_counts_existing_as_duplicates(
        self, auth_client, active_queue, observe_project
    ):
        traces = [
            Trace.objects.create(project=observe_project, name=f"t-{i}")
            for i in range(3)
        ]
        for _t in traces:
            _seed_ch_trace_root(_t)
        # Pre-add one via the enumerated path.
        auth_client.post(
            _add_items_url(active_queue.id),
            {"items": [{"source_type": "trace", "source_id": str(traces[0].id)}]},
            format="json",
        )
        # Filter-add all — expect 2 fresh, 1 duplicate.
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 2
        assert result["duplicates"] == 1
        assert result["total_matching"] == 3

    def test_filter_mode_truncation_returns_400_selection_too_large(
        self, auth_client, active_queue, observe_project
    ):
        # Override the view-level cap for this test so we don't need to
        # seed 10_001 rows.
        import model_hub.views.annotation_queues as views_mod

        original_cap = views_mod.MAX_SELECTION_CAP
        views_mod.MAX_SELECTION_CAP = 2
        try:
            for i in range(3):
                Trace.objects.create(project=observe_project, name=f"t-{i}")
            resp = auth_client.post(
                _add_items_url(active_queue.id),
                {
                    "selection": {
                        "mode": "filter",
                        "source_type": "trace",
                        "project_id": str(observe_project.id),
                    }
                },
                format="json",
            )
        finally:
            views_mod.MAX_SELECTION_CAP = original_cap

        assert resp.status_code == 400, resp.data
        assert resp.data.get("type") == "selection_too_large"
        assert resp.data.get("code") == "selection_too_large"
        assert resp.data.get("detail")
        err = resp.data.get("error") or {}
        assert err.get("type") == "selection_too_large"
        assert err.get("total_matching") == 3
        assert err.get("cap") == 2

    def test_filter_mode_queue_item_count_matches_added(
        self, auth_client, active_queue, observe_project
    ):
        for i in range(4):
            Trace.objects.create(project=observe_project, name=f"t-{i}")

        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 4

        # Verify via a separate GET that the queue actually holds those items.
        list_resp = auth_client.get(
            f"/model-hub/annotation-queues/{active_queue.id}/items/"
        )
        assert list_resp.status_code == 200, list_resp.data
        assert list_resp.data["count"] == 4


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestAddItemsValidation:
    def test_both_items_and_selection_rejected(
        self, auth_client, active_queue, observe_project
    ):
        t = Trace.objects.create(project=observe_project, name="t")
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "items": [{"source_type": "trace", "source_id": str(t.id)}],
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                },
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_neither_items_nor_selection_rejected(self, auth_client, active_queue):
        resp = auth_client.post(_add_items_url(active_queue.id), {}, format="json")
        assert resp.status_code == 400

    def test_unsupported_selection_mode(
        self, auth_client, active_queue, observe_project
    ):
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "ids",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_unsupported_source_type(self, auth_client, active_queue, observe_project):
        # All four source types (trace / observation_span / trace_session /
        # call_execution) are supported after Phase 8. This test keeps the
        # validation path covered by trying an obviously wrong value.
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "dataset_row",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 400


# --------------------------------------------------------------------------
# Phase 4 — filter-mode for source_type=observation_span
# --------------------------------------------------------------------------


def _make_span(project, trace, *, name, offset_minutes=0):
    now = timezone.now()
    return ObservationSpan.objects.create(
        id=f"sp-{name}-{trace.id.hex[:6]}",
        project=project,
        trace=trace,
        name=name,
        observation_type="llm",
        start_time=now + timezone.timedelta(minutes=offset_minutes)
        if hasattr(timezone, "timedelta")
        else now,
        end_time=now,
        parent_span_id=None,
    )


@pytest.fixture
def span_parent_trace(db, observe_project):
    return Trace.objects.create(project=observe_project, name="span-parent")


@pytest.mark.django_db
class TestAddItemsFilterModeSpan:
    def test_filter_mode_span_no_filter_adds_all(
        self, auth_client, active_queue, observe_project, span_parent_trace
    ):
        from datetime import timedelta

        now = timezone.now()
        for i in range(3):
            ObservationSpan.objects.create(
                id=f"sp-{i}-{span_parent_trace.id.hex[:6]}",
                project=observe_project,
                trace=span_parent_trace,
                name=f"sp-{i}",
                observation_type="llm",
                start_time=now - timedelta(minutes=i),
                end_time=now - timedelta(minutes=i) + timedelta(seconds=1),
                parent_span_id=None,
            )

        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "observation_span",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 3
        assert result["duplicates"] == 0
        assert result["total_matching"] == 3

    def test_filter_mode_span_id_filter_adds_exact_span(
        self, auth_client, active_queue, observe_project, span_parent_trace
    ):
        target = _make_span(observe_project, span_parent_trace, name="target")
        other = _make_span(
            observe_project,
            span_parent_trace,
            name="other",
            offset_minutes=1,
        )

        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "observation_span",
                    "project_id": str(observe_project.id),
                    "filter": [_api_filter("span_id", "text", "equals", target.id)],
                }
            },
            format="json",
        )

        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 1
        assert result["total_matching"] == 1
        assert QueueItem.objects.filter(observation_span=target, deleted=False).exists()
        assert not QueueItem.objects.filter(
            observation_span=other, deleted=False
        ).exists()

    def test_filter_mode_span_respects_exclude_ids(
        self, auth_client, active_queue, observe_project, span_parent_trace
    ):
        from datetime import timedelta

        now = timezone.now()
        spans = []
        for i in range(5):
            spans.append(
                ObservationSpan.objects.create(
                    id=f"spx-{i}-{span_parent_trace.id.hex[:6]}",
                    project=observe_project,
                    trace=span_parent_trace,
                    name=f"spx-{i}",
                    observation_type="llm",
                    start_time=now - timedelta(minutes=i),
                    end_time=now - timedelta(minutes=i) + timedelta(seconds=1),
                    parent_span_id=None,
                )
            )
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "observation_span",
                    "project_id": str(observe_project.id),
                    "exclude_ids": [spans[0].id, spans[1].id],
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 3
        assert resp.data["result"]["total_matching"] == 3

    def test_filter_mode_span_duration_filters_on_span_latency(
        self, auth_client, active_queue, observe_project, span_parent_trace
    ):
        now = timezone.now()
        low_latency = ObservationSpan.objects.create(
            id=f"span-low-{span_parent_trace.id.hex[:6]}",
            project=observe_project,
            trace=span_parent_trace,
            name="low-latency",
            observation_type="llm",
            latency_ms=100,
            start_time=now,
            end_time=now,
            parent_span_id=None,
        )
        high_latency = ObservationSpan.objects.create(
            id=f"span-high-{span_parent_trace.id.hex[:6]}",
            project=observe_project,
            trace=span_parent_trace,
            name="high-latency",
            observation_type="llm",
            latency_ms=1000,
            start_time=now + timedelta(minutes=1),
            end_time=now + timedelta(minutes=1),
            parent_span_id=None,
        )

        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "observation_span",
                    "project_id": str(observe_project.id),
                    "filter": [
                        _api_filter("latency_ms", "number", "greater_than", 500)
                    ],
                }
            },
            format="json",
        )

        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 1
        assert resp.data["result"]["total_matching"] == 1
        assert not QueueItem.objects.filter(observation_span=low_latency).exists()
        assert QueueItem.objects.filter(observation_span=high_latency).exists()

    def test_filter_mode_span_counts_existing_as_duplicates(
        self, auth_client, active_queue, observe_project, span_parent_trace
    ):
        from datetime import timedelta

        now = timezone.now()
        spans = []
        for i in range(3):
            span = ObservationSpan.objects.create(
                id=f"spd-{i}-{span_parent_trace.id.hex[:6]}",
                project=observe_project,
                trace=span_parent_trace,
                name=f"spd-{i}",
                observation_type="llm",
                start_time=now - timedelta(minutes=i),
                end_time=now - timedelta(minutes=i) + timedelta(seconds=1),
                parent_span_id=None,
            )
            seed_ch_span(span)
            spans.append(span)
        # Pre-add one via enumerated path
        auth_client.post(
            _add_items_url(active_queue.id),
            {"items": [{"source_type": "observation_span", "source_id": spans[0].id}]},
            format="json",
        )
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "observation_span",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 2
        assert resp.data["result"]["duplicates"] == 1

    def test_filter_mode_span_truncation_returns_400(
        self, auth_client, active_queue, observe_project, span_parent_trace
    ):
        from datetime import timedelta

        import model_hub.views.annotation_queues as views_mod

        original_cap = views_mod.MAX_SELECTION_CAP
        views_mod.MAX_SELECTION_CAP = 2
        try:
            now = timezone.now()
            for i in range(3):
                ObservationSpan.objects.create(
                    id=f"spt-{i}-{span_parent_trace.id.hex[:6]}",
                    project=observe_project,
                    trace=span_parent_trace,
                    name=f"spt-{i}",
                    observation_type="llm",
                    start_time=now - timedelta(minutes=i),
                    end_time=now - timedelta(minutes=i) + timedelta(seconds=1),
                    parent_span_id=None,
                )
            resp = auth_client.post(
                _add_items_url(active_queue.id),
                {
                    "selection": {
                        "mode": "filter",
                        "source_type": "observation_span",
                        "project_id": str(observe_project.id),
                    }
                },
                format="json",
            )
        finally:
            views_mod.MAX_SELECTION_CAP = original_cap

        assert resp.status_code == 400, resp.data
        assert resp.data.get("type") == "selection_too_large"
        assert resp.data.get("code") == "selection_too_large"
        err = resp.data.get("error") or {}
        assert err.get("type") == "selection_too_large"
        assert err.get("total_matching") == 3
        assert err.get("cap") == 2


# --------------------------------------------------------------------------
# Phase 6 — filter-mode for source_type=trace_session
# --------------------------------------------------------------------------


@pytest.fixture
def seeded_sessions_for_dispatch(db, observe_project):
    """3 sessions with spans for endpoint-level session dispatch tests."""
    from datetime import timedelta

    from tracer.models.trace_session import TraceSession

    now = timezone.now()
    sessions = []
    for i in range(3):
        s = TraceSession.objects.create(
            project=observe_project, name=f"ds-{i}", bookmarked=False
        )
        t = Trace.objects.create(project=observe_project, session=s, name=f"dt-{i}")
        ObservationSpan.objects.create(
            id=f"sp-disp-{i}-{s.id.hex[:6]}",
            project=observe_project,
            trace=t,
            name=f"sp-{i}",
            observation_type="llm",
            start_time=now - timedelta(minutes=i),
            end_time=now - timedelta(minutes=i) + timedelta(seconds=1),
            parent_span_id=None,
            cost=0.0,
            total_tokens=0,
        )
        sessions.append(s)
    return sessions


@pytest.mark.django_db
class TestAddItemsFilterModeSession:
    @pytest.fixture(autouse=True)
    def _force_pg_fallback(self, monkeypatch):
        """P3b step2 (PG_ORM_READ_MIGRATION, Slice F): the session filter-mode
        resolver is now CH-first. These endpoint tests seed PG fixtures and
        assert PG semantics for the ADD side, so force the CH-outage PG fallback
        (``_resolve_session_ids_clickhouse`` → ``None``) — the same code,
        relocated. CH-first is covered by the ch_rehearsal integration suite.
        (The list_sessions endpoint these compare against is independently
        CH-backed and unaffected by this patch.)"""
        monkeypatch.setattr(
            "model_hub.services.bulk_selection._resolve_session_ids_clickhouse",
            lambda **kwargs: None,
        )

    def test_filter_mode_session_no_filter_adds_all(
        self, auth_client, active_queue, observe_project, seeded_sessions_for_dispatch
    ):
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace_session",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 3
        assert resp.data["result"]["total_matching"] == 3

    def test_filter_mode_session_id_filter_adds_exact_session(
        self, auth_client, active_queue, observe_project, seeded_sessions_for_dispatch
    ):
        target = seeded_sessions_for_dispatch[0]
        other = seeded_sessions_for_dispatch[1]

        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace_session",
                    "project_id": str(observe_project.id),
                    "filter": [
                        _api_filter("session_id", "text", "equals", str(target.id))
                    ],
                }
            },
            format="json",
        )

        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 1
        assert result["total_matching"] == 1
        assert QueueItem.objects.filter(trace_session=target, deleted=False).exists()
        assert not QueueItem.objects.filter(trace_session=other, deleted=False).exists()

    @pytest.mark.api
    @pytest.mark.skip(
        reason="list_sessions endpoint reads from CH; test fixtures only seed PG"
    )
    def test_filter_mode_session_date_filter_matches_list_endpoint(
        self, auth_client, active_queue, observe_project, seeded_sessions_for_dispatch
    ):
        now = timezone.now()
        recent_start = now - timedelta(days=7)
        recent_end = now + timedelta(days=1)
        recent_sessions = seeded_sessions_for_dispatch[:2]
        old_session = seeded_sessions_for_dispatch[2]

        TraceSession.objects.filter(id=recent_sessions[0].id).update(
            created_at=now - timedelta(days=2)
        )
        TraceSession.objects.filter(id=recent_sessions[1].id).update(
            created_at=now - timedelta(days=1)
        )
        TraceSession.objects.filter(id=old_session.id).update(
            created_at=now - timedelta(days=90)
        )

        filters = [
            _api_filter(
                "created_at",
                "datetime",
                "between",
                [recent_start.isoformat(), recent_end.isoformat()],
            )
        ]
        expected_ids = {str(session.id) for session in recent_sessions}

        list_resp = auth_client.get(
            "/tracer/trace-session/list_sessions/",
            {
                "project_id": str(observe_project.id),
                "filters": json.dumps(filters),
                "sort_params": "[]",
                "page_number": 0,
                "page_size": 20,
            },
        )
        assert list_resp.status_code == 200, list_resp.data
        list_ids = {row["session_id"] for row in list_resp.data["result"]["table"]}
        assert list_ids == expected_ids

        add_resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace_session",
                    "project_id": str(observe_project.id),
                    "filter": filters,
                }
            },
            format="json",
        )
        assert add_resp.status_code == 200, add_resp.data
        assert add_resp.data["result"]["added"] == 2
        assert add_resp.data["result"]["total_matching"] == 2

        queue_session_ids = {
            str(item.trace_session_id)
            for item in QueueItem.objects.filter(
                queue=active_queue,
                source_type="trace_session",
                deleted=False,
            )
        }
        assert queue_session_ids == expected_ids

    def test_filter_mode_session_respects_exclude_ids(
        self, auth_client, active_queue, observe_project, seeded_sessions_for_dispatch
    ):
        exclude = [str(seeded_sessions_for_dispatch[0].id)]
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace_session",
                    "project_id": str(observe_project.id),
                    "exclude_ids": exclude,
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 2

    def test_filter_mode_session_truncation_returns_400(
        self, auth_client, active_queue, observe_project, seeded_sessions_for_dispatch
    ):
        import model_hub.views.annotation_queues as views_mod

        original_cap = views_mod.MAX_SELECTION_CAP
        views_mod.MAX_SELECTION_CAP = 2
        try:
            resp = auth_client.post(
                _add_items_url(active_queue.id),
                {
                    "selection": {
                        "mode": "filter",
                        "source_type": "trace_session",
                        "project_id": str(observe_project.id),
                    }
                },
                format="json",
            )
        finally:
            views_mod.MAX_SELECTION_CAP = original_cap
        assert resp.status_code == 400, resp.data
        assert resp.data.get("type") == "selection_too_large"
        assert resp.data.get("code") == "selection_too_large"
        err = resp.data.get("error") or {}
        assert err.get("type") == "selection_too_large"
        assert err.get("total_matching") == 3
        assert err.get("cap") == 2

    def test_session_list_hides_simulator_project_sessions(
        self, auth_client, organization, workspace
    ):
        simulator_project = Project.objects.create(
            name="Voice Sessions Hidden",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            source=ProjectSourceChoices.SIMULATOR.value,
        )
        session = TraceSession.objects.create(
            project=simulator_project,
            name="voice-session",
        )
        trace = Trace.objects.create(
            project=simulator_project,
            session=session,
            name="voice-trace",
        )
        ObservationSpan.objects.create(
            id=f"voice-session-{trace.id.hex[:8]}",
            project=simulator_project,
            trace=trace,
            name="conversation-root",
            observation_type="conversation",
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(seconds=10),
            parent_span_id=None,
        )

        resp = auth_client.get(
            "/tracer/trace-session/list_sessions/",
            {
                "project_id": str(simulator_project.id),
                "page_number": 0,
                "page_size": 20,
            },
        )

        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["metadata"]["total_rows"] == 0
        assert result["table"] == []

    def test_filter_mode_session_hides_simulator_project_sessions(
        self, auth_client, active_queue, organization, workspace
    ):
        simulator_project = Project.objects.create(
            name="Voice Filter Sessions Hidden",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            source=ProjectSourceChoices.SIMULATOR.value,
        )
        session = TraceSession.objects.create(
            project=simulator_project,
            name="voice-filter-session",
        )
        trace = Trace.objects.create(
            project=simulator_project,
            session=session,
            name="voice-filter-trace",
        )
        ObservationSpan.objects.create(
            id=f"voice-filter-session-{trace.id.hex[:8]}",
            project=simulator_project,
            trace=trace,
            name="conversation-root",
            observation_type="conversation",
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(seconds=10),
            parent_span_id=None,
        )

        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace_session",
                    "project_id": str(simulator_project.id),
                }
            },
            format="json",
        )

        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 0
        assert resp.data["result"]["total_matching"] == 0
        assert not QueueItem.objects.filter(trace_session=session).exists()


# --------------------------------------------------------------------------
# Phase 8 — filter-mode for source_type=call_execution
#
# For call_execution, ``selection.project_id`` is reinterpreted as the
# agent_definition_id — see Phase 8 PRD.
# --------------------------------------------------------------------------


@pytest.fixture
def seeded_call_executions_for_dispatch(db, organization, workspace):
    from simulate.models.agent_definition import AgentDefinition
    from simulate.models.run_test import RunTest
    from simulate.models.scenarios import Scenarios
    from simulate.models.test_execution import CallExecution, TestExecution

    agent_def = AgentDefinition.objects.create(
        agent_name="ce-disp-agent",
        inbound=True,
        description="dispatch fixture",
        organization=organization,
        workspace=workspace,
    )
    run = RunTest.objects.create(name="ce-disp-run", organization=organization)
    te = TestExecution.objects.create(run_test=run, agent_definition=agent_def)
    scen = Scenarios.objects.create(
        name="ce-disp-scenario",
        source="dispatch",
        organization=organization,
        workspace=workspace,
    )
    ces = [
        CallExecution.objects.create(test_execution=te, scenario=scen) for _ in range(3)
    ]
    return agent_def, ces


@pytest.mark.django_db
class TestAddItemsFilterModeCallExecution:
    def test_filter_mode_ce_no_filter_adds_all(
        self, auth_client, active_queue, seeded_call_executions_for_dispatch
    ):
        agent_def, _ = seeded_call_executions_for_dispatch
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "call_execution",
                    "project_id": str(agent_def.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 3
        assert resp.data["result"]["total_matching"] == 3

    def test_filter_mode_ce_respects_exclude_ids(
        self, auth_client, active_queue, seeded_call_executions_for_dispatch
    ):
        agent_def, ces = seeded_call_executions_for_dispatch
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "call_execution",
                    "project_id": str(agent_def.id),
                    "exclude_ids": [str(ces[0].id)],
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 2

    def test_filter_mode_ce_truncation_returns_400(
        self, auth_client, active_queue, seeded_call_executions_for_dispatch
    ):
        import model_hub.views.annotation_queues as views_mod

        agent_def, _ = seeded_call_executions_for_dispatch
        original_cap = views_mod.MAX_SELECTION_CAP
        views_mod.MAX_SELECTION_CAP = 2
        try:
            resp = auth_client.post(
                _add_items_url(active_queue.id),
                {
                    "selection": {
                        "mode": "filter",
                        "source_type": "call_execution",
                        "project_id": str(agent_def.id),
                    }
                },
                format="json",
            )
        finally:
            views_mod.MAX_SELECTION_CAP = original_cap
        assert resp.status_code == 400, resp.data
        assert resp.data.get("type") == "selection_too_large"
        assert resp.data.get("code") == "selection_too_large"
        err = resp.data.get("error") or {}
        assert err.get("type") == "selection_too_large"
        assert err.get("total_matching") == 3
        assert err.get("cap") == 2


# --------------------------------------------------------------------------
# Manual add-items + filter-mode + non-created_at filters on call_execution.
#
# Before _apply_call_execution_filters existed, the resolver only honored
# created_at filters and silently match-all'd everything else. The fixture
# below seeds calls with mixed status/duration so we can prove status
# and duration_seconds filters now actually narrow the result.
# --------------------------------------------------------------------------


@pytest.fixture
def seeded_mixed_call_executions(db, organization, workspace):
    from model_hub.models.choices import SourceChoices
    from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
    from simulate.models.agent_definition import AgentDefinition
    from simulate.models.run_test import RunTest
    from simulate.models.scenarios import Scenarios
    from simulate.models.test_execution import CallExecution, TestExecution

    agent_def = AgentDefinition.objects.create(
        agent_name="ce-mixed-agent",
        inbound=True,
        description="mixed-status fixture",
        organization=organization,
        workspace=workspace,
    )
    run = RunTest.objects.create(name="ce-mixed-run", organization=organization)
    te = TestExecution.objects.create(run_test=run, agent_definition=agent_def)
    dataset = Dataset.objects.create(
        name="ce-mixed-scenario-dataset",
        organization=organization,
        workspace=workspace,
    )
    priority_column = Column.objects.create(
        name="priority",
        data_type="text",
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    attempts_column = Column.objects.create(
        name="attempts",
        data_type="integer",
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(priority_column.id), str(attempts_column.id)]
    dataset.save(update_fields=["column_order"])
    high_priority_row = Row.objects.create(dataset=dataset, order=1)
    low_priority_row = Row.objects.create(dataset=dataset, order=2)
    failed_row = Row.objects.create(dataset=dataset, order=3)
    Cell.objects.create(
        dataset=dataset,
        column=priority_column,
        row=high_priority_row,
        value="high",
    )
    Cell.objects.create(
        dataset=dataset,
        column=attempts_column,
        row=high_priority_row,
        value="2",
    )
    Cell.objects.create(
        dataset=dataset,
        column=priority_column,
        row=low_priority_row,
        value="low",
    )
    Cell.objects.create(
        dataset=dataset,
        column=attempts_column,
        row=low_priority_row,
        value="8",
    )
    Cell.objects.create(
        dataset=dataset,
        column=priority_column,
        row=failed_row,
        value="high",
    )
    Cell.objects.create(
        dataset=dataset,
        column=attempts_column,
        row=failed_row,
        value="4",
    )
    scen = Scenarios.objects.create(
        name="ce-mixed-scenario",
        source="mixed",
        organization=organization,
        workspace=workspace,
        dataset=dataset,
    )
    te.scenario_ids = [str(scen.id)]
    te.execution_metadata = {
        "Provider": True,
        "column_order": [
            {
                "id": str(priority_column.id),
                "column_name": "priority",
                "visible": True,
                "data_type": "text",
                "type": "scenario_dataset_column",
                "scenario_id": str(scen.id),
                "dataset_id": str(dataset.id),
            },
            {
                "id": str(attempts_column.id),
                "column_name": "attempts",
                "visible": True,
                "data_type": "integer",
                "type": "scenario_dataset_column",
                "scenario_id": str(scen.id),
                "dataset_id": str(dataset.id),
            },
            {
                "id": "tool_eval_accuracy",
                "column_name": "Tool Accuracy",
                "visible": True,
                "type": "tool_evaluation",
            },
        ],
    }
    te.save(update_fields=["scenario_ids", "execution_metadata"])
    completed_short = CallExecution.objects.create(
        test_execution=te,
        scenario=scen,
        status="completed",
        duration_seconds=10,
        cost_cents=12,
        row_id=high_priority_row.id,
        call_metadata={
            "row_data": {
                "persona": {
                    "name": "Casey",
                    "language": "English",
                    "languages": ["English"],
                    "communication_style": ["Direct and concise"],
                    "age_group": ["25-32"],
                    "multilingual": False,
                }
            }
        },
        tool_outputs={"tool_eval_accuracy": {"output": "pass"}},
    )
    completed_long = CallExecution.objects.create(
        test_execution=te,
        scenario=scen,
        status="completed",
        duration_seconds=120,
        customer_cost_cents=120,
        row_id=low_priority_row.id,
        call_metadata={
            "row_data": {
                "persona": {
                    "name": "Riya",
                    "language": "Hindi",
                    "languages": ["Hindi", "English"],
                    "communication_style": ["Casual and friendly"],
                    "age_group": ["32-40"],
                    "multilingual": True,
                }
            }
        },
        tool_outputs={"tool_eval_accuracy": {"output": "fail"}},
    )
    failed = CallExecution.objects.create(
        test_execution=te,
        scenario=scen,
        status="failed",
        duration_seconds=30,
        cost_cents=56,
        row_id=failed_row.id,
        call_metadata={
            "row_data": {
                "persona": {
                    "name": "Jordan",
                    "language": "English",
                    "languages": ["English"],
                    "communication_style": ["Detailed and elaborate"],
                    "age_group": ["40-50"],
                    "multilingual": False,
                }
            }
        },
        tool_outputs={"tool_eval_accuracy": {"output": "pass"}},
    )
    return (
        agent_def,
        te,
        completed_short,
        completed_long,
        failed,
        priority_column,
        attempts_column,
    )


@pytest.mark.django_db
class TestAddItemsFilterModeCallExecutionRichFilters:
    def test_simulation_add_items_grid_endpoint_applies_rules_style_filters(
        self, auth_client, seeded_mixed_call_executions
    ):
        _, test_execution, completed_short, *_ = seeded_mixed_call_executions

        resp = auth_client.get(
            f"/simulate/test-executions/{test_execution.id}/",
            {
                "filters": json.dumps(
                    [
                        _api_filter(
                            "status",
                            "categorical",
                            "equals",
                            "completed",
                        ),
                        _api_filter(
                            "duration_seconds",
                            "number",
                            "less_than",
                            60,
                        ),
                    ]
                ),
                "page": 1,
                "limit": 20,
            },
        )

        assert resp.status_code == 200, resp.data
        assert resp.data["count"] == 1
        assert [row["id"] for row in resp.data["results"]] == [str(completed_short.id)]

    def test_simulation_add_items_grid_endpoint_filters_scenario_attributes(
        self, auth_client, seeded_mixed_call_executions
    ):
        (
            _agent_def,
            test_execution,
            completed_short,
            _completed_long,
            failed,
            priority_column,
            attempts_column,
        ) = seeded_mixed_call_executions

        resp = auth_client.get(
            f"/simulate/test-executions/{test_execution.id}/",
            {
                "filters": json.dumps(
                    [
                        _api_filter(
                            str(priority_column.id),
                            "text",
                            "equals",
                            "high",
                        ),
                        _api_filter(
                            str(attempts_column.id),
                            "number",
                            "less_than",
                            5,
                        ),
                    ]
                ),
                "page": 1,
                "limit": 20,
            },
        )

        assert resp.status_code == 200, resp.data
        assert resp.data["count"] == 2
        assert {row["id"] for row in resp.data["results"]} == {
            str(completed_short.id),
            str(failed.id),
        }

    def test_simulation_add_items_grid_endpoint_filters_tool_eval_columns(
        self, auth_client, seeded_mixed_call_executions
    ):
        _, test_execution, completed_short, _completed_long, failed, *_ = (
            seeded_mixed_call_executions
        )

        resp = auth_client.get(
            f"/simulate/test-executions/{test_execution.id}/",
            {
                "filters": json.dumps(
                    [
                        _api_filter(
                            "tool_eval_accuracy",
                            "text",
                            "equals",
                            "pass",
                        )
                    ]
                ),
                "page": 1,
                "limit": 20,
            },
        )

        assert resp.status_code == 200, resp.data
        assert resp.data["count"] == 2
        assert {row["id"] for row in resp.data["results"]} == {
            str(completed_short.id),
            str(failed.id),
        }

    def test_simulation_add_items_grid_endpoint_filters_system_cost_metric(
        self, auth_client, seeded_mixed_call_executions
    ):
        _, test_execution, completed_short, *_ = seeded_mixed_call_executions

        resp = auth_client.get(
            f"/simulate/test-executions/{test_execution.id}/",
            {
                "filters": json.dumps(
                    [_api_filter("cost_cents", "number", "less_than", 20)]
                ),
                "page": 1,
                "limit": 20,
            },
        )

        assert resp.status_code == 200, resp.data
        assert resp.data["count"] == 1
        assert [row["id"] for row in resp.data["results"]] == [str(completed_short.id)]

    def test_simulation_add_items_grid_endpoint_filters_persona_fields(
        self, auth_client, seeded_mixed_call_executions
    ):
        _, test_execution, _completed_short, completed_long, *_ = (
            seeded_mixed_call_executions
        )

        resp = auth_client.get(
            f"/simulate/test-executions/{test_execution.id}/",
            {
                "filters": json.dumps(
                    [
                        _api_filter(
                            "persona.language",
                            "categorical",
                            "equals",
                            "Hindi",
                        ),
                        _api_filter(
                            "persona.multilingual",
                            "boolean",
                            "equals",
                            True,
                        ),
                    ]
                ),
                "page": 1,
                "limit": 20,
            },
        )

        assert resp.status_code == 200, resp.data
        assert resp.data["count"] == 1
        assert [row["id"] for row in resp.data["results"]] == [str(completed_long.id)]

    def test_filter_mode_status_filter_narrows_result(
        self, auth_client, active_queue, seeded_mixed_call_executions
    ):
        agent_def, *_ = seeded_mixed_call_executions
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "call_execution",
                    "project_id": str(agent_def.id),
                    "filter": [
                        {
                            "column_id": "status",
                            "filter_config": {
                                "filter_type": "categorical",
                                "filter_op": "equals",
                                "filter_value": "completed",
                            },
                        }
                    ],
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        # Only the 2 completed calls — NOT the failed one. Pre-fix this
        # would have added all 3 because the filter was silently dropped.
        assert resp.data["result"]["added"] == 2
        assert resp.data["result"]["total_matching"] == 2

    def test_filter_mode_duration_range_narrows_result(
        self, auth_client, active_queue, seeded_mixed_call_executions
    ):
        agent_def, *_ = seeded_mixed_call_executions
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "call_execution",
                    "project_id": str(agent_def.id),
                    "filter": [
                        {
                            "column_id": "duration_seconds",
                            "filter_config": {
                                "filter_type": "number",
                                "filter_op": "less_than",
                                "filter_value": 60,
                            },
                        }
                    ],
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        # 10s + 30s match; 120s excluded.
        assert resp.data["result"]["added"] == 2
        assert resp.data["result"]["total_matching"] == 2

    def test_filter_mode_persona_field_narrows_result(
        self, auth_client, active_queue, seeded_mixed_call_executions
    ):
        agent_def, *_ = seeded_mixed_call_executions
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "call_execution",
                    "project_id": str(agent_def.id),
                    "filter": [
                        {
                            "column_id": "persona.communication_style",
                            "filter_config": {
                                "filter_type": "categorical",
                                "filter_op": "equals",
                                "filter_value": "Direct and concise",
                            },
                        }
                    ],
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 1
        assert resp.data["result"]["total_matching"] == 1

    def test_filter_mode_unsupported_column_returns_400(
        self, auth_client, active_queue, seeded_mixed_call_executions
    ):
        agent_def, *_ = seeded_mixed_call_executions
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "call_execution",
                    "project_id": str(agent_def.id),
                    "filter": [
                        {
                            "column_id": "totally_made_up_column",
                            "filter_config": {
                                "filter_type": "text",
                                "filter_op": "equals",
                                "filter_value": "x",
                            },
                        }
                    ],
                }
            },
            format="json",
        )
        # ValueError from resolver -> bad_request. Better than the old
        # silent match-all behaviour.
        assert resp.status_code == 400, resp.data
        body = resp.data.get("result") or resp.data.get("message") or ""
        assert "totally_made_up_column" in str(body) or "cannot apply" in str(body)

    def test_filter_mode_combined_status_and_duration(
        self, auth_client, active_queue, seeded_mixed_call_executions
    ):
        agent_def, *_ = seeded_mixed_call_executions
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "call_execution",
                    "project_id": str(agent_def.id),
                    "filter": [
                        {
                            "column_id": "status",
                            "filter_config": {
                                "filter_type": "categorical",
                                "filter_op": "equals",
                                "filter_value": "completed",
                            },
                        },
                        {
                            "column_id": "duration_seconds",
                            "filter_config": {
                                "filter_type": "number",
                                "filter_op": "less_than",
                                "filter_value": 60,
                            },
                        },
                    ],
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        # Only completed_short (10s, completed) matches both filters.
        assert resp.data["result"]["added"] == 1
        assert resp.data["result"]["total_matching"] == 1
