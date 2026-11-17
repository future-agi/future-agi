"""Collector-only Imagine context and explicit rerun regressions."""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from asgiref.sync import async_to_sync, sync_to_async

from tfc.temporal.imagine.activities import fetch_trace_data
from tfc.temporal.imagine.types import FetchTraceInput
from tracer.models.imagine_analysis import ImagineAnalysis
from tracer.models.trace import Trace
from tracer.tests.test_imagine_analysis_api import (
    IMAGINE_ANALYSIS_PATH,
    create_imagine_saved_view,
    result,
)


@pytest.fixture
def collector_scope(observe_project, workspace, user, organization, monkeypatch):
    trace_id = str(uuid.uuid4())
    saved_view = create_imagine_saved_view(observe_project, workspace, user)
    span = SimpleNamespace(
        name="collector-root",
        observation_type="llm",
        latency_ms=12,
        total_tokens=7,
        model="test-model",
        status="OK",
        input="collector input",
        output="collector output",
    )
    reader = Mock()

    def read_spans(requested_trace, *, project_id=None, include_heavy=True):
        if requested_trace == trace_id and project_id == str(observe_project.id):
            return [span]
        return []

    reader.list_by_trace.side_effect = read_spans
    factory = Mock(return_value=reader)
    starter = Mock()
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", factory)
    monkeypatch.setattr("channels.db.database_sync_to_async", sync_to_async)
    monkeypatch.setattr("tfc.temporal.imagine.client.start_imagine_analysis", starter)
    assert not Trace.no_workspace_objects.filter(id=trace_id).exists()
    return SimpleNamespace(
        trace_id=trace_id,
        saved_view=saved_view,
        project=observe_project,
        organization=organization,
        reader=reader,
        factory=factory,
        starter=starter,
    )


def trigger(client, scope, **extra):
    return client.post(
        IMAGINE_ANALYSIS_PATH,
        {
            "saved_view_id": str(scope.saved_view.id),
            "trace_id": scope.trace_id,
            "project_id": str(scope.project.id),
            "widgets": [{"widget_id": "summary", "prompt": "Fresh summary"}],
            **extra,
        },
        format="json",
    )


def poll(client, scope):
    return client.get(
        IMAGINE_ANALYSIS_PATH,
        {"saved_view_id": str(scope.saved_view.id), "trace_id": scope.trace_id},
    )


def completed_analysis(scope):
    analysis = ImagineAnalysis.no_workspace_objects.create(
        saved_view=scope.saved_view,
        project=scope.project,
        organization=scope.organization,
        trace_id=scope.trace_id,
        widget_id="summary",
        prompt="Old prompt",
        status="completed",
        content="Old summary",
    )
    analysis.workflow_id = f"imagine-pb1-{analysis.id}"
    analysis.save(update_fields=["workflow_id", "updated_at"])
    return analysis


@pytest.mark.django_db
def test_collector_only_trace_triggers_and_polls(auth_client, collector_scope):
    scope = collector_scope
    response = trigger(auth_client, scope)
    assert response.status_code == 200
    assert result(response)["analyses"][0]["status"] == "running"
    assert scope.starter.call_args.kwargs["project_id"] == str(scope.project.id)
    assert scope.starter.call_args.kwargs["trace_id"] == scope.trace_id
    analysis = ImagineAnalysis.no_workspace_objects.get(saved_view=scope.saved_view)
    analysis.status = "completed"
    analysis.content = "Collector summary"
    analysis.save(update_fields=["status", "content", "updated_at"])
    response = poll(auth_client, scope)
    assert response.status_code == 200
    assert result(response)["analyses"][0]["content"] == "Collector summary"
    assert scope.reader.list_by_trace.call_args_list == [
        call(scope.trace_id, project_id=str(scope.project.id), include_heavy=False),
        call(scope.trace_id, project_id=str(scope.project.id), include_heavy=False),
    ]
    assert scope.reader.close.call_count == 2


@pytest.mark.django_db
def test_collector_only_worker_builds_project_bound_context(collector_scope):
    scope = collector_scope
    context = async_to_sync(fetch_trace_data)(
        FetchTraceInput(
            trace_id=scope.trace_id,
            org_id=str(scope.organization.id),
            project_id=str(scope.project.id),
        )
    )
    for expected in (
        f"Trace ID: {scope.trace_id}",
        f"Project: {scope.project.name}",
        "collector-root",
        "12ms latency, 7 tokens",
        "Input: collector input",
        "Output: collector output",
    ):
        assert expected in context
    scope.reader.list_by_trace.assert_called_once_with(
        scope.trace_id, project_id=str(scope.project.id)
    )
    scope.reader.close.assert_called_once()


@pytest.mark.django_db
def test_collector_absence_does_not_admit_or_return_cached_result(
    auth_client, collector_scope
):
    scope = collector_scope
    scope.reader.list_by_trace.side_effect = lambda *args, **kwargs: []
    completed_analysis(scope)
    assert trigger(auth_client, scope).status_code == 404
    assert poll(auth_client, scope).status_code == 404
    scope.starter.assert_not_called()
    assert scope.reader.close.call_count == 2


@pytest.mark.django_db
def test_collector_worker_checks_organization_before_clickhouse(collector_scope):
    scope = collector_scope
    context = async_to_sync(fetch_trace_data)(
        FetchTraceInput(
            trace_id=scope.trace_id,
            org_id=str(uuid.uuid4()),
            project_id=str(scope.project.id),
        )
    )
    assert context == f"Trace {scope.trace_id} not found."
    scope.factory.assert_not_called()


@pytest.mark.django_db
def test_collector_worker_closes_reader_on_error(collector_scope):
    scope = collector_scope
    scope.reader.list_by_trace.side_effect = RuntimeError("reader unavailable")
    with pytest.raises(RuntimeError, match="reader unavailable"):
        async_to_sync(fetch_trace_data)(
            FetchTraceInput(
                trace_id=scope.trace_id,
                org_id=str(scope.organization.id),
                project_id=str(scope.project.id),
            )
        )
    scope.reader.close.assert_called_once()


@pytest.mark.django_db
def test_explicit_rerun_bypasses_completed_cache(auth_client, collector_scope):
    scope = collector_scope
    # Use a legacy row so this regression isolates rerun from collector admission.
    Trace.no_workspace_objects.create(id=scope.trace_id, project=scope.project)
    analysis = completed_analysis(scope)
    response = trigger(auth_client, scope, force=True)
    assert response.status_code == 200
    assert result(response)["analyses"][0]["status"] == "running"
    scope.starter.assert_called_once()
    assert scope.starter.call_args.kwargs["prompt"] == "Fresh summary"
    analysis.refresh_from_db()
    assert analysis.prompt == "Fresh summary"
    assert analysis.content is None
    assert analysis.error is None
    assert analysis.status == "running"


@pytest.mark.django_db
def test_rerun_keeps_active_run_dedup_and_ordinary_cache(auth_client, collector_scope):
    scope = collector_scope
    Trace.no_workspace_objects.create(id=scope.trace_id, project=scope.project)
    analysis = completed_analysis(scope)
    response = trigger(auth_client, scope)
    assert response.status_code == 200
    assert result(response)["analyses"][0]["content"] == "Old summary"
    scope.starter.assert_not_called()
    analysis.status = "running"
    analysis.save(update_fields=["status", "updated_at"])
    response = trigger(auth_client, scope, force=True)
    assert response.status_code == 200
    assert result(response)["analyses"][0]["status"] == "running"
    scope.starter.assert_not_called()
