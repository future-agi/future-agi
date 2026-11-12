"""Fail closed for Imagine activities already queued in Temporal history.

Construct new bindings by assignment so every revision reaches each
assertion instead of failing on a new constructor keyword. Reuse the
real project/analysis models; stub only the LLM and Temporal boundaries.
"""

import sys
import uuid
from dataclasses import fields
from datetime import timedelta
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from temporalio.exceptions import ApplicationError

from tfc.temporal.imagine.activities import run_llm_analysis, save_analysis_result
from tfc.temporal.imagine.types import (
    ImagineAnalysisInput,
    RunAnalysisInput,
    SaveResultInput,
)
from tfc.temporal.imagine.workflows import ImagineAnalysisWorkflow
from tracer.models.imagine_analysis import ImagineAnalysis
from tracer.tests.test_imagine_analysis_api import create_imagine_saved_view
from tracer.tests.test_imagine_analysis_trace_scope import (
    imagine_scope as imagine_scope,
)


@pytest.fixture
def llm_boundary(monkeypatch):
    async def completion(*_args, **_kwargs):
        yield {
            "choices": [
                {"delta": {"content": "bound summary"}, "finish_reason": "stop"}
            ]
        }

    stream = Mock(side_effect=completion)
    factory = Mock(return_value=SimpleNamespace(stream_completion=stream))
    module = ModuleType("ee.falcon_ai.llm_client")
    module.FalconLLMClient = factory
    monkeypatch.setitem(sys.modules, module.__name__, module)
    gate = Mock()
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", gate)
    return SimpleNamespace(factory=factory, stream=stream, gate=gate)


@pytest.fixture
def save_runtime(monkeypatch):
    # Preserve the real ORM and thread handoff without closing the test's DB
    # transaction. This is the same adapter used by the trace-scope tests.
    monkeypatch.setattr("channels.db.database_sync_to_async", sync_to_async)
    info = SimpleNamespace(workflow_id="unrelated-workflow")
    monkeypatch.setattr("tfc.temporal.imagine.activities.activity.info", lambda: info)
    return info


@pytest.fixture
def analysis_rows(imagine_scope, user):
    rows = {}
    traces = {"local": imagine_scope.local_trace, **imagine_scope.foreign_traces}
    for name, trace in traces.items():
        saved_view = create_imagine_saved_view(
            trace.project, trace.project.workspace, user
        )
        analysis_id = uuid.uuid4()
        rows[name] = ImagineAnalysis.no_workspace_objects.create(
            id=analysis_id,
            workflow_id=f"imagine-pb1-{analysis_id}",
            saved_view=saved_view,
            project=trace.project,
            organization=imagine_scope.organization,
            widget_id="summary",
            trace_id=str(trace.id),
            prompt="Summarize this trace.",
            content="previous summary",
            status="running",
        )
    assert all(
        ImagineAnalysis.no_workspace_objects.filter(id=row.id).exists()
        for row in rows.values()
    ), "SETUP_AQ-XWS-W3C analysis rows exist before invoking the real activity"
    assert rows["local"].project_id == imagine_scope.project.id, (
        "SETUP_AQ-XWS-W3C local analysis uses the admitted project"
    )
    return rows


def _bound_save_input(analysis_id, scope):
    input = SaveResultInput(
        analysis_id=str(analysis_id), content="queued summary", status="completed"
    )
    input.project_id = str(scope.project.id)
    input.org_id = str(scope.organization.id)
    return input


@pytest.mark.parametrize("payload_type", [RunAnalysisInput, SaveResultInput])
def test_activity_payloads_have_history_compatible_project_binding(payload_type):
    project_fields = [
        field for field in fields(payload_type) if field.name == "project_id"
    ]
    assert len(project_fields) == 1 and project_fields[0].default is None, (
        "AQ-XWS-W3C: downstream payloads must decode legacy history with project_id=None"
    )


@pytest.mark.parametrize("binding", [None, ""])
def test_queued_llm_refuses_missing_or_empty_binding(llm_boundary, binding):
    input = RunAnalysisInput(
        prompt="Summarize this trace.",
        trace_context="persisted foreign trace context",
        org_id=str(uuid.uuid4()),
    )
    if binding == "":
        input.project_id = binding
    refusal = None
    try:
        async_to_sync(run_llm_analysis)(input)
    except ApplicationError as exc:
        refusal = (exc.type, exc.non_retryable, exc.message)
    assert (
        refusal,
        llm_boundary.factory.call_count,
        llm_boundary.stream.call_count,
    ) == (
        ("ImagineProjectBindingMissing", True, "Imagine project binding missing."),
        0,
        0,
    ), "AQ-XWS-W3C: unbound queued LLM must refuse non-retryably before any client call"


def test_bound_llm_still_submits_context(llm_boundary):
    input = RunAnalysisInput(
        prompt="Summarize this trace.",
        trace_context="authorized trace context",
        org_id=str(uuid.uuid4()),
    )
    input.project_id = str(uuid.uuid4())
    content = async_to_sync(run_llm_analysis)(input)
    assert (
        content,
        llm_boundary.factory.call_count,
        llm_boundary.stream.call_count,
    ) == (
        "bound summary",
        1,
        1,
    ), "AQ-XWS-W3C: bound queued LLM must still call the client and return its output"
    messages = llm_boundary.stream.call_args.args[0]
    assert input.trace_context in messages[0]["content"], (
        "AQ-XWS-W3C: bound LLM positive control must submit the actual trace context"
    )
    assert llm_boundary.gate.call_args.kwargs == {
        "org_id": input.org_id,
        "activity": True,
    }, "AQ-XWS-W3C: bound LLM must retain organization capability gating"


@pytest.mark.parametrize("llm_fails", [False, True])
def test_workflow_forwards_binding_without_changing_commands(monkeypatch, llm_fails):
    scheduled = []
    failure = RuntimeError("stubbed LLM failure")

    async def execute_activity(name, input, **kwargs):
        scheduled.append((name, input, kwargs))
        if name == "run_llm_analysis" and llm_fails:
            raise failure
        return {
            "fetch_trace_data": "historical trace context",
            "run_llm_analysis": "bound summary",
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
        project_id=str(uuid.uuid4()),
    )
    caught = None
    try:
        async_to_sync(ImagineAnalysisWorkflow().run)(input)
    except RuntimeError as exc:
        caught = exc
    assert caught is (failure if llm_fails else None), (
        "SETUP_AQ-XWS-W3C workflow must exercise the selected success or failure path"
    )
    assert [name for name, _, _ in scheduled] == [
        "fetch_trace_data",
        "run_llm_analysis",
        "save_analysis_result",
    ], "AQ-XWS-W3C: workflow must preserve the existing three activity commands"
    assert [options["start_to_close_timeout"] for _, _, options in scheduled] == [
        timedelta(seconds=30),
        timedelta(seconds=90),
        timedelta(seconds=10),
    ], "AQ-XWS-W3C: workflow must preserve activity timeouts"
    assert [
        (getattr(payload, "project_id", None), getattr(payload, "org_id", None))
        for _, payload, _ in scheduled
    ] == [(input.project_id, input.org_id)] * 3, (
        "AQ-XWS-W3C: fetch, LLM, and both save paths must carry project and organization"
    )
    assert scheduled[2][1].status == ("failed" if llm_fails else "completed"), (
        "AQ-XWS-W3C: workflow must preserve existing success and failure save statuses"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("binding", [None, "", "missing_org"])
def test_queued_save_without_binding_discards_content_and_marks_failed(
    imagine_scope, analysis_rows, save_runtime, binding
):
    row = analysis_rows["local"]
    save_runtime.workflow_id = row.workflow_id
    input = SaveResultInput(analysis_id=str(row.id), content="queued foreign summary")
    if binding == "":
        input.project_id = ""
    elif binding == "missing_org":
        input.project_id = str(imagine_scope.project.id)
    async_to_sync(save_analysis_result)(input)
    row.refresh_from_db()
    assert (row.content, row.status, row.error) == (
        "",
        "failed",
        "Imagine project binding missing.",
    ), (
        "AQ-XWS-W3C: unbound queued save must discard content and persist a fixed failure"
    )


@pytest.mark.django_db
def test_unbound_save_cannot_mark_another_workflows_analysis_failed(
    analysis_rows, save_runtime
):
    row = analysis_rows["local"]
    assert save_runtime.workflow_id != row.workflow_id, (
        "SETUP_AQ-XWS-W3C cleanup targets a different persisted workflow"
    )
    input = SaveResultInput(analysis_id=str(row.id), content="queued foreign summary")
    async_to_sync(save_analysis_result)(input)
    row.refresh_from_db()
    assert (row.content, row.status, row.error) == (
        "previous summary",
        "running",
        None,
    ), "AQ-XWS-W3C: legacy cleanup must be bound to the executing workflow"


@pytest.mark.django_db
@pytest.mark.parametrize("foreign_scope", ["other_project", "other_workspace"])
def test_foreign_and_nonexistent_save_have_identical_refused_shape(
    imagine_scope, analysis_rows, save_runtime, foreign_scope
):
    row = analysis_rows[foreign_scope]
    save_runtime.workflow_id = row.workflow_id
    assert row.project_id != imagine_scope.project.id, (
        "SETUP_AQ-XWS-W3C foreign analysis is outside the admitted project"
    )
    missing_id = uuid.uuid4()
    assert not ImagineAnalysis.no_workspace_objects.filter(id=missing_id).exists(), (
        "SETUP_AQ-XWS-W3C nonexistent analysis is absent"
    )
    foreign_result = async_to_sync(save_analysis_result)(
        _bound_save_input(row.id, imagine_scope)
    )
    missing_result = async_to_sync(save_analysis_result)(
        _bound_save_input(missing_id, imagine_scope)
    )
    row.refresh_from_db()
    assert (foreign_result, missing_result, row.content, row.status, row.error) == (
        None,
        None,
        "previous summary",
        "running",
        None,
    ), (
        "AQ-XWS-W3C: foreign and nonexistent saves must return alike without writing content"
    )


@pytest.mark.django_db
def test_bound_save_rechecks_organization(imagine_scope, analysis_rows, save_runtime):
    row = analysis_rows["local"]
    save_runtime.workflow_id = row.workflow_id
    input = _bound_save_input(row.id, imagine_scope)
    input.org_id = str(uuid.uuid4())
    assert input.org_id != str(row.organization_id), (
        "SETUP_AQ-XWS-W3C organization differs despite matching project"
    )
    async_to_sync(save_analysis_result)(input)
    row.refresh_from_db()
    assert (row.content, row.status, row.error) == (
        "previous summary",
        "running",
        None,
    ), "AQ-XWS-W3C: matching project must not bypass the persisted organization binding"


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["completed", "failed"])
def test_same_scope_save_still_persists_result(
    imagine_scope, analysis_rows, save_runtime, status
):
    row = analysis_rows["local"]
    save_runtime.workflow_id = row.workflow_id
    input = _bound_save_input(row.id, imagine_scope)
    input.status = status
    input.error = "LLM analysis failed after retries" if status == "failed" else None
    input.content = "" if status == "failed" else "bound summary"
    async_to_sync(save_analysis_result)(input)
    row.refresh_from_db()
    assert (row.content, row.status, row.error) == (
        input.content,
        input.status,
        input.error,
    ), "AQ-XWS-W3C: same-scope success and failure saves must remain available"
