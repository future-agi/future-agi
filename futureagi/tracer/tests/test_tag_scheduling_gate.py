"""Public graph actions reject deterministic tag failures before enqueueing.

Real assignment rows and ClickHouse liveness probes exercise the 2,049-key
boundary; cache/worker transport is isolated so enqueue counts are observable.
"""

from contextlib import nullcontext
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from tracer.models.entity_tag_assignment import EntityTagAssignment
from tracer.services.clickhouse import graph_dispatch
from tracer.services.clickhouse.v2 import effective_tags
from tracer.services.clickhouse.v2.entity_tag_targets import TRACE_ASSIGNMENT_HOUR
from tracer.tests.test_effective_tag_filters import filters, leaf
from tracer.tests.test_entity_tag_assignment_postgres import assignment_schema  # noqa: F401
from tracer.tests.test_entity_tag_target import PROJECT, START
from tracer.tests.test_selected_root_tag_graphs import roots_store  # noqa: F401

ORG = "22222222-2222-4222-8222-222222222222"
WORKSPACE = "33333333-3333-4333-8333-333333333333"


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.fixture
def stores(request):
    from django.db import connection

    request.getfixturevalue("assignment_schema")
    store = request.getfixturevalue("roots_store")
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM tracer_entity_tag_assignment")
    return SimpleNamespace(
        execute=store.execute,
        seed=store.insert,
        analytics=SimpleNamespace(execute_ch_query=store.query),
    )


@pytest.fixture
def live_assignments(stores):
    # The shared native CH25 fixture uses production traces.id UUID and the
    # deployed six-component span replacement key.

    ids = [str(UUID(int=index + 1)) for index in range(2049)]
    stores.execute(
        """INSERT INTO spans
        (project_id, trace_id, id, observation_type, service_name, start_time,
         parent_span_id, _version)
        SELECT %(project)s, arrayJoin(%(traces)s), 'root', 'span', 'svc',
               %(start)s, '', 1""",
        {"project": PROJECT, "traces": ids, "start": START},
    )
    EntityTagAssignment.objects.bulk_create(
        [
            EntityTagAssignment(
                project_id=PROJECT,
                entity_kind="trace",
                trace_id=trace,
                span_id="",
                start_hour=TRACE_ASSIGNMENT_HOUR,
                observation_type="",
                service_name="",
                manual_tags=[{"name": "wanted"}],
            )
            for trace in ids
        ]
    )
    return stores


def _complete(surface):
    data = (
        {"nodes": [], "edges": [], "path_edges": []}
        if surface == "agent"
        else {
            "metric_name": "active_users",
            "data": [],
        }
    )
    return {
        **data,
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
    }


def _identity(surface):
    identity = {
        "project_id": PROJECT,
        "filters": filters(leaf("tag", "wanted")),
        "organization_id": ORG,
        "workspace_id": WORKSPACE,
        "payload_version": graph_dispatch.AGENT_GRAPH_PAYLOAD_VERSION
        if surface == "agent"
        else graph_dispatch.OBSERVE_SYSTEM_GRAPH_PAYLOAD_VERSION,
    }
    if surface == "users":
        identity.update(interval="hour", metric_id="active_users")
    return identity


def _cache_transport(monkeypatch, surface, *, cached=False, running=False):
    from django.core.cache.backends.locmem import LocMemCache

    from tracer.services import exact_aggregation_cache as snapshots

    isolated_cache = LocMemCache("tag-scheduling-transport", {})
    isolated_cache.clear()
    monkeypatch.setattr(snapshots, "cache", isolated_cache)
    monkeypatch.setattr(
        snapshots,
        "_configured_exact_aggregation_task_queue",
        lambda: "exact_aggregation",
    )
    scheduled = []

    def enqueue(namespace, identity, token, queue):
        scheduled.append((namespace, identity, token, queue))
        return True

    monkeypatch.setattr(snapshots, "_enqueue_exact_refresh", enqueue)
    namespace = (
        "observe-agent-graph" if surface == "agent" else "observe-user-system-graph"
    )
    identity = snapshots.normalize_exact_observe_identity(_identity(surface))
    if cached:
        snapshots.publish_exact_snapshot(namespace, identity, _complete(surface))
    if running:
        assert snapshots.begin_exact_refresh(namespace, identity) is not None
    return scheduled


def _endpoint(monkeypatch, surface, *, refresh=False, analytics=None):
    from tracer.views import project as project_view
    from tracer.views import trace as trace_view

    query_filters = filters(leaf("tag", "wanted"))
    scope = SimpleNamespace(organization_id=ORG)
    request = SimpleNamespace(
        validated_query_data={
            "project_id": PROJECT,
            "filters": query_filters,
            "refresh": refresh,
        },
        validated_data={
            "project_id": PROJECT,
            "filters": query_filters,
            "interval": "hour",
            "req_data_config": {"id": "active_users", "type": "SYSTEM_METRIC"},
        },
        workspace=SimpleNamespace(id=WORKSPACE),
    )
    if surface == "agent":
        projects = MagicMock()
        projects.filter.return_value.first.return_value = scope
        monkeypatch.setattr(
            trace_view, "_project_queryset_for_request", lambda _request: projects
        )
        monkeypatch.setattr(
            trace_view, "bind_request_my_annotations_principal", lambda _r, f: f
        )
        view = trace_view.TraceView()
        action = trace_view.TraceView.agent_graph
    else:
        monkeypatch.setattr(
            project_view,
            "graph_action_postgres_budget",
            lambda _deadline: nullcontext(),
        )
        monkeypatch.setattr(
            project_view, "bind_request_my_annotations_principal", lambda _r, f: f
        )
        monkeypatch.setattr(project_view, "V2AnalyticsQueryService", lambda: analytics)
        view = project_view.ProjectView()
        monkeypatch.setattr(view, "_get_project_in_scope", lambda _project: scope)
        action = project_view.ProjectView.get_users_aggregate_graph_data
    view.request = request
    return unwrap(action)(view, request)


@pytest.mark.parametrize(
    "surface,cached,refresh",
    [("agent", False, False), ("agent", True, True), ("users", True, True)],
)
def test_2049_live_assignments_return_typed_400_without_scheduling(
    live_assignments, monkeypatch, surface, cached, refresh
):
    assert effective_tags.MAX_TAG_OVERLAY_KEYS == 2048
    scheduled = _cache_transport(monkeypatch, surface, cached=cached)
    response = _endpoint(
        monkeypatch, surface, refresh=refresh, analytics=live_assignments.analytics
    )
    assert response.status_code == 400, {
        "response": response.data,
        "enqueue_count": len(scheduled),
    }
    assert response.data["code"] == "tag_filter_too_broad"
    assert "Tag filter" in str(response.data)
    assert scheduled == []


@pytest.mark.unit
@pytest.mark.parametrize("surface", ["agent", "users"])
@pytest.mark.parametrize("running", [False, True])
def test_complete_cache_poll_does_not_scan_tags_or_schedule(
    monkeypatch, surface, running
):
    scheduled = _cache_transport(monkeypatch, surface, cached=True, running=running)
    monkeypatch.setattr(
        effective_tags,
        "graph_overlay_executor",
        lambda: pytest.fail("cache polling must not scan tags"),
    )
    response = _endpoint(monkeypatch, surface)
    assert response.status_code == 200, response.data
    assert response.data["result"]["query_status"] == "complete"
    assert scheduled == []


@pytest.mark.unit
def test_agent_pending_poll_reconciles_dispatch_without_rescanning_tags(monkeypatch):
    from tracer.services import exact_aggregation_cache as snapshots

    scheduled = _cache_transport(monkeypatch, "agent", running=True)
    reconcile = MagicMock(return_value=False)
    monkeypatch.setattr(snapshots, "_release_terminal_dispatch_claim", reconcile)
    monkeypatch.setattr(
        effective_tags,
        "graph_overlay_executor",
        lambda: pytest.fail("pending polls must not scan tags"),
    )
    response = _endpoint(monkeypatch, "agent")
    assert response.status_code == 200, response.data
    assert response.data["result"]["query_refreshing"] is True
    reconcile.assert_called_once()
    assert scheduled == []


@pytest.mark.parametrize(
    "surface,cached,refresh",
    [("agent", False, False), ("agent", True, True), ("users", True, True)],
)
def test_small_live_overlay_schedules_once(
    stores, monkeypatch, surface, cached, refresh
):
    trace = str(UUID(int=1))
    stores.seed(trace_id=trace, start_time=START, tags="[]", attrs_string={})
    EntityTagAssignment.objects.create(
        project_id=PROJECT,
        entity_kind="trace",
        trace_id=trace,
        span_id="",
        start_hour=TRACE_ASSIGNMENT_HOUR,
        observation_type="",
        service_name="",
        manual_tags=[{"name": "wanted"}],
    )
    scheduled = _cache_transport(monkeypatch, surface, cached=cached)
    response = _endpoint(
        monkeypatch, surface, refresh=refresh, analytics=stores.analytics
    )
    assert response.status_code == 200, response.data
    assert len(scheduled) == 1


@pytest.mark.unit
@pytest.mark.parametrize("failure_kind", ["graph", "overlay", "unavailable"])
def test_single_user_graph_preserves_tag_error_status(monkeypatch, failure_kind):
    from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
        GraphTagFilterTooBroadError,
    )
    from tracer.services.entity_tags import TagReadUnavailable
    from tracer.tests import test_user_session_graph_action_deadline as users

    failures = {
        "graph": GraphTagFilterTooBroadError(),
        "overlay": TagReadUnavailable("tag_overlay_budget_exceeded"),
        "unavailable": TagReadUnavailable("tag_source_unavailable"),
    }
    monkeypatch.setattr(
        users._UserDetailBuilder, "build", MagicMock(side_effect=failures[failure_kind])
    )
    response, _events, _dispatched = users._call_project_user_action(
        monkeypatch,
        "get_user_graph_data",
        deadline_values=[60_000, 60_000],
    )
    assert response.status_code == (503 if failure_kind == "unavailable" else 400), (
        response.data
    )
    assert response.data["code"] == (
        "service_unavailable"
        if failure_kind == "unavailable"
        else "tag_filter_too_broad"
    )


@pytest.mark.unit
@pytest.mark.parametrize("surface", ["agent", "users"])
@pytest.mark.parametrize("failure_kind", ["graph", "overlay"])
def test_worker_tag_failure_survives_cache_envelope_and_endpoint_poll(
    monkeypatch, settings, surface, failure_kind
):
    from django.core.cache import cache

    from tracer.services import exact_aggregation_cache as snapshots
    from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
        GraphTagFilterTooBroadError,
    )
    from tracer.services.entity_tags import TagReadUnavailable
    from tracer.tasks import exact_aggregation as worker

    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "tag-scheduling-gate",
        }
    }
    cache.clear()
    namespace = (
        "observe-agent-graph" if surface == "agent" else "observe-user-system-graph"
    )
    identity = snapshots.normalize_exact_observe_identity(_identity(surface))
    token = snapshots.begin_exact_refresh(namespace, identity)
    assert token is not None
    failure = (
        GraphTagFilterTooBroadError()
        if failure_kind == "graph"
        else TagReadUnavailable("tag_overlay_budget_exceeded")
    )
    monkeypatch.setattr(worker, "_load_exact_payload", MagicMock(side_effect=failure))
    monkeypatch.setattr(worker, "Thread", MagicMock())
    with pytest.raises(GraphTagFilterTooBroadError):
        worker.refresh_exact_aggregation_snapshot.run_sync(
            namespace=namespace,
            identity=identity,
            refresh_token=token,
        )
    envelope = snapshots.read_or_schedule_exact_snapshot(
        namespace,
        identity,
        refresh=False,
        pending_payload={"query_status": "pending"},
        schedule_on_miss=False,
    )
    assert envelope["query_error_code"] == "tag_filter_too_broad"
    assert envelope["query_refresh_failed"] is True
    enqueue = MagicMock(
        side_effect=AssertionError("a failed worker must not be re-enqueued by a poll")
    )
    monkeypatch.setattr(snapshots, "_enqueue_exact_refresh", enqueue)
    monkeypatch.setattr(
        effective_tags,
        "graph_overlay_executor",
        lambda: pytest.fail("failed poll must not re-read tag assignments"),
    )
    response = _endpoint(monkeypatch, surface)
    assert response.status_code == 400, response.data
    assert response.data["code"] == "tag_filter_too_broad"
    enqueue.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    "error_code", ["tag_filter_too_broad", "private SQL and tenant diagnostic"]
)
def test_failure_code_is_allowlisted_and_fenced(monkeypatch, settings, error_code):
    from django.core.cache import cache

    from tracer.services import exact_aggregation_cache as snapshots

    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "tag-failure-fence",
        }
    }
    cache.clear()
    namespace, identity = "observe-agent-graph", {"project_id": PROJECT}
    token = snapshots.begin_exact_refresh(namespace, identity)
    assert token is not None
    snapshots.finish_exact_refresh(
        namespace, identity, "old-worker", succeeded=False, error_code=error_code
    )
    assert snapshots.exact_refresh_state(namespace, identity) == "running"
    snapshots.finish_exact_refresh(
        namespace, identity, token, succeeded=False, error_code=error_code
    )
    record = snapshots._exact_refresh_state_record(namespace, identity)
    assert record["status"] == "failed"
    if error_code == "tag_filter_too_broad":
        assert record["error_code"] == error_code
    else:
        assert "error_code" not in record
        assert "private" not in str(record)


@pytest.mark.unit
@pytest.mark.parametrize("surface", ["agent", "users"])
def test_explicit_refresh_can_retry_a_previous_typed_failure(monkeypatch, surface):
    from tracer.services import exact_aggregation_cache as snapshots

    scheduled = _cache_transport(monkeypatch, surface, cached=True)
    namespace = (
        "observe-agent-graph" if surface == "agent" else "observe-user-system-graph"
    )
    identity = snapshots.normalize_exact_observe_identity(_identity(surface))
    token = snapshots.begin_exact_refresh(namespace, identity)
    snapshots.finish_exact_refresh(
        namespace, identity, token, succeeded=False, error_code="tag_filter_too_broad"
    )
    preflight = MagicMock()
    monkeypatch.setattr(
        graph_dispatch,
        "_preflight_scheduled_graph_tags"
        if surface == "agent"
        else "_preflight_user_graph_tags",
        preflight,
    )
    response = _endpoint(monkeypatch, surface, refresh=True)
    assert response.status_code == 200, response.data
    assert "query_error_code" not in response.data["result"]
    assert len(scheduled) == 1
    preflight.assert_called_once()
