"""Authorized Agent Graph HTTP preflight, with primary PG and native CH25.

Run on the disposable localhost stack with TAG_ASSIGNMENT_DISPOSABLE_PG=1.
Authentication is forced at DRF's supported boundary, not by unwrapping the
view: URL resolution, permissions, request validation, project authorization,
preflight/error conversion and response rendering all run their real code.
Only the exact-cache/worker transport is isolated; no worker is dispatched.
"""

import json
import os
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from django.core.cache.backends.locmem import LocMemCache
from django.db import DatabaseError, connection
from django.urls import resolve
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from tracer.models.entity_tag_assignment import EntityTagAssignment
from tracer.models.project import Project
from tracer.services import exact_aggregation_cache as snapshots
from tracer.services.clickhouse import graph_dispatch
from tracer.services.clickhouse.bounded_graph_reads import BoundedGraphReadError
from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
    GraphTagFilterTooBroadError,
)
from tracer.services.clickhouse.v2 import effective_tags
from tracer.services.clickhouse.v2.entity_tag_targets import TRACE_ASSIGNMENT_HOUR
from tracer.tests.test_effective_tag_filters import leaf
from tracer.tests.test_selected_root_tag_graphs import (
    PROJECT,
    START,
    roots_store,  # noqa: F401
    window,
)

ROUTE = "/tracer/trace/agent_graph/"
ORG = "55555555-5555-4555-8555-555555555555"
WORKSPACE = "66666666-6666-4666-8666-666666666666"
NAMESPACE = "observe-agent-graph"
FILTERS = window(leaf("tag", "wanted"))
COMPLETE = {
    "nodes": [],
    "edges": [],
    "path_edges": [],
    "query_complete": True,
    "query_status": "complete",
    "query_sampled": False,
}


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.fixture
def authorized_store(django_db_blocker, request):
    """Real scoped Project and assignment tables, without legacy PG telemetry.

    Principal objects represent the already-authenticated tenant (as with
    force_authenticate elsewhere). Project authorization is not mocked. Build
    its column definitions from the model, omitting unrelated FK constraints
    so this fixture does not require the full accounts/simulation schema.
    """
    if os.environ.get("TAG_ASSIGNMENT_DISPOSABLE_PG") != "1":
        pytest.skip("Requires explicitly provisioned disposable PostgreSQL")
    assert connection.settings_dict["HOST"] in {"localhost", "127.0.0.1"}
    assert connection.settings_dict["NAME"].startswith("test_th7275")
    schema = "agent_preflight_" + uuid4().hex
    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE SCHEMA "{schema}"')
            cursor.execute(f'SET search_path TO "{schema}"')
        try:
            with connection.schema_editor() as editor:
                columns, column_params = [], []
                for field in Project._meta.local_fields:
                    definition, params = editor.column_sql(Project, field)
                    columns.append(f"{editor.quote_name(field.column)} {definition}")
                    column_params.extend(params)
                editor.execute(
                    "CREATE TABLE tracer_project (" + ", ".join(columns) + ")",
                    column_params,
                )
                editor.create_model(EntityTagAssignment)
            Project.no_workspace_objects.bulk_create(
                [
                    Project(
                        id=PROJECT,
                        organization_id=ORG,
                        workspace_id=WORKSPACE,
                        name="Agent preflight synthetic project",
                        model_type="generative_llm",
                        trace_type="observe",
                    )
                ]
            )
            yield request.getfixturevalue("roots_store")
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SET search_path TO public")
                cursor.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.fixture
def transport(monkeypatch):
    cache = LocMemCache("agent-preflight-" + uuid4().hex, {})
    monkeypatch.setattr(snapshots, "cache", cache)
    monkeypatch.setattr(
        snapshots,
        "_configured_exact_aggregation_task_queue",
        lambda: "exact_aggregation",
    )
    enqueue = Mock(return_value=True)
    monkeypatch.setattr(snapshots, "_enqueue_exact_refresh", enqueue)
    identity = snapshots.normalize_exact_observe_identity(
        {
            "project_id": PROJECT,
            "filters": FILTERS,
            "organization_id": ORG,
            "workspace_id": WORKSPACE,
            "payload_version": graph_dispatch.AGENT_GRAPH_PAYLOAD_VERSION,
        }
    )

    def prime(*, cached=False, running=False):
        if cached:
            snapshots.publish_exact_snapshot(NAMESPACE, identity, dict(COMPLETE))
        if running:
            assert snapshots.begin_exact_refresh(NAMESPACE, identity) is not None

    return SimpleNamespace(enqueue=enqueue, identity=identity, prime=prime)


def request_graph(
    method,
    *,
    refresh=False,
    organization_id=ORG,
    workspace_id=WORKSPACE,
    authenticated=True,
):
    """Invoke the registered HTTP route, retaining its DRF and validation wrappers."""
    factory = APIRequestFactory()
    payload = {"project_id": PROJECT, "filters": FILTERS, "refresh": refresh}
    if method == "get":
        payload["filters"] = json.dumps(FILTERS)
        payload["refresh"] = "true" if refresh else "false"
        request = factory.get(ROUTE, payload)
    else:
        request = factory.post(ROUTE, payload, format="json")
    organization = Organization(id=organization_id)
    request.organization = organization
    request.workspace = Workspace(
        id=workspace_id, organization=organization, is_default=False
    )
    if authenticated:
        force_authenticate(request, user=User(id=uuid4(), organization=organization))
    route = resolve(ROUTE)
    assert route.func.actions[method] == "agent_graph"
    response = route.func(request, *route.args, **route.kwargs)
    response.render()
    return response


def seed_assignments(store, count=1):
    ids = [str(UUID(int=index + 1)) for index in range(count)]
    store.execute(
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


def error_body(code, error_type, message):
    return {
        "status": False,
        "type": error_type,
        "code": code,
        "detail": message,
        "message": message,
        "error": message,
        "result": message,
    }


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("cached,refresh", [(False, False), (True, True)])
def test_primary_pg_timeout_is_retryable_503_after_authorization(
    authorized_store, transport, monkeypatch, method, cached, refresh
):
    seed_assignments(authorized_store)
    transport.prime(cached=cached)
    previous_snapshot = snapshots.read_exact_snapshot(NAMESPACE, transport.identity)
    events = []
    failures = []
    preflight = graph_dispatch._preflight_scheduled_graph_tags

    def observe_preflight(identity):
        try:
            return preflight(identity)
        except BoundedGraphReadError as exc:
            failures.append((exc.error_code, exc.retryable))
            raise

    monkeypatch.setattr(
        graph_dispatch, "_preflight_scheduled_graph_tags", observe_preflight
    )

    def timeout_assignment_select(execute, sql, params, many, context):
        if sql.lstrip().upper().startswith("SELECT"):
            if 'FROM "tracer_project"' in sql:
                events.append("project_authorization")
            elif 'FROM "tracer_entity_tag_assignment"' in sql:
                assert events == ["project_authorization"]
                assert context["connection"].alias == "default"
                events.append("primary_assignment_timeout")
                # A real primary-PG cancellation at the assignment SELECT I/O
                # boundary. The production read policy installs its statement
                # deadline on this statement; the sleep deliberately exceeds it.
                try:
                    return execute("SELECT pg_sleep(10)", None, False, context)
                except DatabaseError as exc:
                    assert exc.__cause__.sqlstate == "57014"  # query_canceled
                    raise
        return execute(sql, params, many, context)

    with connection.execute_wrapper(timeout_assignment_select):
        response = request_graph(method, refresh=refresh)
    assert events == ["project_authorization", "primary_assignment_timeout"]
    assert failures == [("query_failed", True)]
    transport.enqueue.assert_not_called()
    assert snapshots.exact_refresh_state(NAMESPACE, transport.identity) is None
    assert (
        snapshots.read_exact_snapshot(NAMESPACE, transport.identity)
        == previous_snapshot
    )
    assert response.status_code == 503, response.data
    assert json.loads(response.content) == error_body(
        "service_unavailable",
        "service_unavailable",
        "Agent graph data is temporarily unavailable. Please retry.",
    )


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("cached,refresh", [(False, False), (True, True)])
def test_2049_live_assignments_stay_permanent_400_without_scheduling(
    authorized_store, transport, method, cached, refresh
):
    assert effective_tags.MAX_TAG_OVERLAY_KEYS == 2048
    seed_assignments(authorized_store, count=2049)
    transport.prime(cached=cached)
    response = request_graph(method, refresh=refresh)
    transport.enqueue.assert_not_called()
    assert response.status_code == 400, response.data
    assert json.loads(response.content) == error_body(
        "tag_filter_too_broad", "validation_error", GraphTagFilterTooBroadError.message
    )


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("cached,refresh", [(False, False), (True, True)])
def test_within_budget_preflight_schedules_once(
    authorized_store, transport, method, cached, refresh
):
    seed_assignments(authorized_store)
    transport.prime(cached=cached)
    response = request_graph(method, refresh=refresh)
    assert response.status_code == 200, response.data
    assert response.data["result"]["query_status"] == (
        "complete" if cached else "pending"
    )
    assert response.data["result"]["query_refreshing"] is True
    transport.enqueue.assert_called_once()
    namespace, identity, _token, queue = transport.enqueue.call_args.args
    assert namespace == NAMESPACE
    assert identity == transport.identity
    assert queue == "exact_aggregation"


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize(
    "cached,running,refresh",
    [
        (True, False, False),
        (True, True, False),
        (True, True, True),
        (False, True, False),
        (False, True, True),
    ],
)
def test_cached_and_running_requests_do_not_preflight_or_schedule(
    authorized_store, transport, monkeypatch, method, cached, running, refresh
):
    transport.prime(cached=cached, running=running)
    preflight = Mock(
        side_effect=AssertionError("cached/running request rescanned tags")
    )
    monkeypatch.setattr(graph_dispatch, "_preflight_scheduled_graph_tags", preflight)
    reconcile = Mock(return_value=False)
    monkeypatch.setattr(snapshots, "_release_terminal_dispatch_claim", reconcile)
    response = request_graph(method, refresh=refresh)
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["query_status"] == ("complete" if cached else "pending")
    assert result["query_complete"] is cached
    assert result["query_refreshing"] is running
    preflight.assert_not_called()
    transport.enqueue.assert_not_called()
    if running and (refresh or not cached):
        reconcile.assert_called_once()


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize(
    "failure",
    [RuntimeError("private diagnostic"), BoundedGraphReadError("query_failed")],
)
def test_unexpected_and_nonretryable_failures_remain_sanitized_500(
    authorized_store, transport, monkeypatch, method, failure
):
    preflight = Mock(side_effect=failure)
    monkeypatch.setattr(graph_dispatch, "_preflight_scheduled_graph_tags", preflight)
    response = request_graph(method)
    preflight.assert_called_once()
    transport.enqueue.assert_not_called()
    assert response.status_code == 500, response.data
    assert json.loads(response.content) == error_body(
        "server_error", "server_error", "Agent graph data could not be loaded"
    )


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("denial", ["organization", "workspace", "unauthenticated"])
def test_authorization_precedes_tag_reads_and_scheduling(
    authorized_store, transport, monkeypatch, method, denial
):
    preflight = Mock(side_effect=AssertionError("unauthorized tag preflight"))
    monkeypatch.setattr(graph_dispatch, "_preflight_scheduled_graph_tags", preflight)
    scope = {}
    if denial == "organization":
        scope["organization_id"] = str(uuid4())
    elif denial == "workspace":
        scope["workspace_id"] = str(uuid4())
    else:
        scope["authenticated"] = False
    response = request_graph(method, **scope)
    assert response.status_code == (401 if denial == "unauthenticated" else 400), (
        response.data
    )
    preflight.assert_not_called()
    transport.enqueue.assert_not_called()
    if denial != "unauthenticated":
        assert response.data["detail"] == "Project not found"
