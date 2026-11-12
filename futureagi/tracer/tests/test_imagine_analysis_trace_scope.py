"""Project-bound Imagine admission, polling, and worker regressions.

Keep the scope inputs constructible without the project binding, so a failure
reaches the scope assertions rather than a constructor TypeError.
Temporal and ClickHouse are stubbed; the normal tracer DB fixtures are reused.
"""

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tfc.temporal.imagine.activities import fetch_trace_data
from tfc.temporal.imagine.types import FetchTraceInput, ImagineAnalysisInput
from tracer.models.imagine_analysis import ImagineAnalysis
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.tests.test_imagine_analysis_api import (
    IMAGINE_ANALYSIS_PATH,
    create_imagine_saved_view,
    result,
)


@pytest.fixture
def imagine_scope(observe_project, workspace, user, organization):
    saved_view = create_imagine_saved_view(observe_project, workspace, user)
    other_workspace = Workspace.no_workspace_objects.create(
        name="Imagine scope other workspace",
        organization=organization,
        is_active=True,
        created_by=user,
    )
    traces = {}
    for key, target_workspace in (
        ("other_project", workspace),
        ("other_workspace", other_workspace),
    ):
        project = Project.no_workspace_objects.create(
            name=f"Imagine scope {key}",
            organization=organization,
            workspace=target_workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        traces[key] = Trace.no_workspace_objects.create(
            project=project, name=f"Foreign {key} trace"
        )
        assert project.id != observe_project.id, "SETUP_AQ-XWS-W3 distinct projects"
        assert project.organization_id == organization.id, (
            "SETUP_AQ-XWS-W3 foreign projects share the request organization"
        )
    assert traces["other_project"].project.workspace_id == workspace.id, (
        "SETUP_AQ-XWS-W3 same-workspace foreign project"
    )
    assert traces["other_workspace"].project.workspace_id != workspace.id, (
        "SETUP_AQ-XWS-W3 different-workspace foreign project"
    )
    local_trace = Trace.no_workspace_objects.create(
        project=observe_project, name="Allowed Imagine trace"
    )
    assert saved_view.project_id == local_trace.project_id, (
        "SETUP_AQ-XWS-W3 saved view and local trace share the authorized project"
    )
    return SimpleNamespace(
        saved_view=saved_view,
        project=observe_project,
        organization=organization,
        local_trace=local_trace,
        foreign_traces=traces,
    )


@pytest.fixture
def imagine_starter(monkeypatch):
    starter = Mock(return_value="imagine-scope-workflow")
    monkeypatch.setattr("tfc.temporal.imagine.client.start_imagine_analysis", starter)
    return starter


@pytest.fixture
def imagine_reader(monkeypatch):
    # Retain async/sync thread handoff, but not channels' connection cleanup:
    # closing connections here would invalidate pytest-django's transaction.
    monkeypatch.setattr("channels.db.database_sync_to_async", sync_to_async)
    reader = Mock()
    reader.list_by_trace.return_value = [
        SimpleNamespace(
            name="scope-root-span",
            observation_type="llm",
            latency_ms=12,
            total_tokens=7,
            model="scope-test-model",
            status="OK",
            input="scope-root-input",
            output="scope-root-output",
        )
    ]
    factory = Mock(return_value=reader)
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", factory)
    return SimpleNamespace(reader=reader, factory=factory)


def _post(auth_client, scope, trace):
    return auth_client.post(
        IMAGINE_ANALYSIS_PATH,
        data={
            "saved_view_id": str(scope.saved_view.id),
            "trace_id": str(trace.id),
            "project_id": str(scope.project.id),
            "widgets": [{"widget_id": "summary", "prompt": "Summarize this trace."}],
        },
        format="json",
    )


def _insert_analysis(scope, trace):
    analysis = ImagineAnalysis.no_workspace_objects.create(
        saved_view=scope.saved_view,
        project=scope.project,
        organization=scope.organization,
        widget_id="summary",
        trace_id=str(trace.id),
        prompt="Summarize this trace.",
        content="foreign-result-must-not-be-returned",
        status="completed",
    )
    assert ImagineAnalysis.no_workspace_objects.filter(id=analysis.id).exists(), (
        "SETUP_AQ-XWS-W3 analysis persisted before the worker or poll"
    )
    return analysis


def _bound_fetch_input(trace_id, org_id, project_id):
    input = FetchTraceInput(trace_id=str(trace_id), org_id=str(org_id))
    # The base dataclass has no project_id constructor parameter, but is not
    # slotted. Attach it by assignment so the lookup under test actually runs.
    input.project_id = str(project_id)
    return input


@pytest.mark.django_db
@pytest.mark.parametrize("foreign_scope", ["other_workspace", "other_project"])
def test_post_rejects_foreign_trace_without_side_effects(
    auth_client, imagine_scope, imagine_starter, foreign_scope
):
    trace = imagine_scope.foreign_traces[foreign_scope]
    response = _post(auth_client, imagine_scope, trace)
    observed = (
        response.status_code,
        ImagineAnalysis.no_workspace_objects.filter(
            saved_view=imagine_scope.saved_view
        ).count(),
        imagine_starter.call_count,
    )
    assert observed == (status.HTTP_404_NOT_FOUND, 0, 0), (
        f"AQ-XWS-W3: POST {foreign_scope} trace must be refused before analysis or workflow"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["post", "get"])
@pytest.mark.parametrize("foreign_scope", ["other_workspace", "other_project"])
def test_cached_foreign_trace_result_is_not_returned(
    auth_client, imagine_scope, imagine_starter, foreign_scope, method
):
    trace = imagine_scope.foreign_traces[foreign_scope]
    analysis = _insert_analysis(imagine_scope, trace)
    analysis.workflow_id = f"imagine-pb1-{analysis.id}"
    analysis.save(update_fields=["workflow_id", "updated_at"])
    if method == "post":
        response = _post(auth_client, imagine_scope, trace)
    else:
        response = auth_client.get(
            IMAGINE_ANALYSIS_PATH,
            data={
                "saved_view_id": str(imagine_scope.saved_view.id),
                "trace_id": str(trace.id),
            },
        )
    observed = (
        response.status_code,
        analysis.content in str(response.json()),
        imagine_starter.call_count,
    )
    assert observed == (status.HTTP_404_NOT_FOUND, False, 0), (
        f"AQ-XWS-W3: {method.upper()} must not return cached {foreign_scope} trace content"
    )


@pytest.mark.django_db
def test_post_persists_and_dispatches_authorized_project(
    auth_client, imagine_scope, imagine_starter
):
    response = _post(auth_client, imagine_scope, imagine_scope.local_trace)
    assert response.status_code == status.HTTP_200_OK, (
        "SETUP_AQ-XWS-W3 allowed trace admission succeeds"
    )
    assert imagine_starter.call_count == 1, (
        "SETUP_AQ-XWS-W3 allowed admission reaches the workflow starter"
    )
    analysis = ImagineAnalysis.no_workspace_objects.get(
        saved_view=imagine_scope.saved_view, trace_id=str(imagine_scope.local_trace.id)
    )
    dispatched = imagine_starter.call_args.kwargs
    assert (
        analysis.project_id,
        analysis.organization_id,
        dispatched.get("project_id"),
        dispatched.get("org_id"),
    ) == (
        imagine_scope.saved_view.project_id,
        imagine_scope.organization.id,
        str(imagine_scope.saved_view.project_id),
        str(imagine_scope.organization.id),
    ), (
        "AQ-XWS-W3: admission must persist and dispatch the authorized project and organization"
    )


@pytest.mark.django_db
def test_same_project_poll_and_cached_post_remain_available(
    auth_client, imagine_scope, imagine_starter
):
    analysis = _insert_analysis(imagine_scope, imagine_scope.local_trace)
    analysis.content = "allowed cached summary"
    analysis.workflow_id = f"imagine-pb1-{analysis.id}"
    analysis.save(update_fields=["content", "workflow_id", "updated_at"])
    poll = auth_client.get(
        IMAGINE_ANALYSIS_PATH,
        data={
            "saved_view_id": str(imagine_scope.saved_view.id),
            "trace_id": str(imagine_scope.local_trace.id),
        },
    )
    cached = _post(auth_client, imagine_scope, imagine_scope.local_trace)
    assert poll.status_code == cached.status_code == status.HTTP_200_OK, (
        "AQ-XWS-W3: same-project poll and cached POST remain available"
    )
    assert (
        result(poll)["analyses"][0]["content"],
        result(cached)["analyses"][0]["content"],
        imagine_starter.call_count,
    ) == (analysis.content, analysis.content, 0), (
        "AQ-XWS-W3: same-project cached content is returned without another workflow"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("foreign_scope", ["other_workspace", "other_project"])
def test_worker_rejects_foreign_trace_from_persisted_analysis(
    imagine_scope, imagine_reader, foreign_scope
):
    trace = imagine_scope.foreign_traces[foreign_scope]
    analysis = _insert_analysis(imagine_scope, trace)
    input = _bound_fetch_input(
        analysis.trace_id, analysis.organization_id, analysis.project_id
    )
    context = async_to_sync(fetch_trace_data)(input)
    assert (
        context,
        imagine_reader.factory.call_count,
        imagine_reader.reader.list_by_trace.call_count,
    ) == (f"Trace {trace.id} not found.", 0, 0), (
        f"AQ-XWS-W3: worker must reject persisted {foreign_scope} trace before ClickHouse"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("binding", [None, ""])
def test_worker_without_project_binding_fails_closed(
    imagine_scope, imagine_reader, binding
):
    trace = imagine_scope.local_trace
    input = FetchTraceInput(
        trace_id=str(trace.id), org_id=str(imagine_scope.organization.id)
    )
    if binding == "":
        input.project_id = binding
    context = async_to_sync(fetch_trace_data)(input)
    assert (
        context,
        imagine_reader.factory.call_count,
        imagine_reader.reader.list_by_trace.call_count,
    ) == (f"Trace {trace.id} not found.", 0, 0), (
        "AQ-XWS-W3: missing or empty project binding must fail closed before ClickHouse"
    )


@pytest.mark.django_db
def test_worker_keeps_organization_binding(imagine_scope, imagine_reader):
    other_org_id = uuid.uuid4()
    assert other_org_id != imagine_scope.organization.id, (
        "SETUP_AQ-XWS-W3 mismatched organization id"
    )
    input = _bound_fetch_input(
        imagine_scope.local_trace.id, other_org_id, imagine_scope.project.id
    )
    context = async_to_sync(fetch_trace_data)(input)
    assert (context, imagine_reader.factory.call_count) == (
        f"Trace {imagine_scope.local_trace.id} not found.",
        0,
    ), "AQ-XWS-W3: matching project must not bypass the organization binding"


@pytest.mark.django_db
def test_worker_scopes_clickhouse_by_project(imagine_scope, imagine_reader):
    input = _bound_fetch_input(
        imagine_scope.local_trace.id,
        imagine_scope.organization.id,
        imagine_scope.project.id,
    )
    async_to_sync(fetch_trace_data)(input)
    assert imagine_reader.reader.list_by_trace.call_args_list == [
        call(
            str(imagine_scope.local_trace.id), project_id=str(imagine_scope.project.id)
        )
    ], "AQ-XWS-W3: ClickHouse trace reads must carry the authorized project_id"


@pytest.mark.django_db
def test_same_project_worker_still_produces_context(imagine_scope, imagine_reader):
    input = _bound_fetch_input(
        imagine_scope.local_trace.id,
        imagine_scope.organization.id,
        imagine_scope.project.id,
    )
    context = async_to_sync(fetch_trace_data)(input)
    assert all(
        expected in context
        for expected in (
            f"Trace ID: {imagine_scope.local_trace.id}",
            f"Project: {imagine_scope.project.name}",
            "scope-root-span",
            "12ms latency, 7 tokens",
            "Input: scope-root-input",
            "Output: scope-root-output",
        )
    ), "AQ-XWS-W3: same-project trace and root span still produce analysis context"
    assert imagine_reader.reader.close.call_count == 1, (
        "AQ-XWS-W3: same-project ClickHouse reader is closed"
    )


def test_starter_transports_project_binding(monkeypatch):
    from tfc.temporal.imagine.client import start_imagine_analysis

    dispatch = Mock()
    monkeypatch.setattr("tfc.temporal.imagine.client.start_workflow_sync", dispatch)
    project_id, org_id = str(uuid.uuid4()), str(uuid.uuid4())
    kwargs = {
        "analysis_id": str(uuid.uuid4()),
        "trace_id": str(uuid.uuid4()),
        "org_id": org_id,
        "prompt": "Summarize this trace.",
    }
    # Let the base starter execute, rather than failing on a new keyword.
    if "project_id" in inspect.signature(start_imagine_analysis).parameters:
        kwargs["project_id"] = project_id
    start_imagine_analysis(**kwargs)
    assert dispatch.call_count == 1, "SETUP_AQ-XWS-W3 starter reaches dispatch"
    workflow_input = dispatch.call_args.args[1]
    assert (getattr(workflow_input, "project_id", None), workflow_input.org_id) == (
        project_id,
        org_id,
    ), "AQ-XWS-W3: starter must carry project and organization into workflow input"


def test_workflow_transports_project_binding(monkeypatch):
    from tfc.temporal.imagine.workflows import ImagineAnalysisWorkflow

    activities = []

    async def execute_activity(name, input, **kwargs):
        activities.append((name, input))
        return {
            "fetch_trace_data": "allowed context",
            "run_llm_analysis": "allowed summary",
            "save_analysis_result": None,
        }[name]

    monkeypatch.setattr(
        "tfc.temporal.imagine.workflows.workflow.execute_activity", execute_activity
    )
    input = ImagineAnalysisInput(
        analysis_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        org_id=str(uuid.uuid4()),
        prompt="Summarize this trace.",
    )
    input.project_id = str(uuid.uuid4())
    async_to_sync(ImagineAnalysisWorkflow().run)(input)
    assert activities and activities[0][0] == "fetch_trace_data", (
        "SETUP_AQ-XWS-W3 workflow schedules trace fetch"
    )
    fetch_input = activities[0][1]
    assert (getattr(fetch_input, "project_id", None), fetch_input.org_id) == (
        input.project_id,
        input.org_id,
    ), "AQ-XWS-W3: workflow must carry project and organization into trace activity"
