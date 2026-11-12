"""Versioned provenance for cached Imagine summaries and activity writes.

The legacy fixture represents a summary produced before project-scoped
ClickHouse reads: PostgreSQL owns trace T in P, but a sibling project's spans
also carry T. Only external Temporal/ClickHouse boundaries are replaced; the
admission, cache, fetch, and save guards run unchanged against real ORM rows.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from asgiref.sync import async_to_sync, sync_to_async

from tfc.temporal.imagine.activities import fetch_trace_data, save_analysis_result
from tfc.temporal.imagine.client import start_imagine_analysis
from tfc.temporal.imagine.types import FetchTraceInput, SaveResultInput
from tfc.temporal.imagine.workflows import ImagineAnalysisWorkflow
from tracer.models.imagine_analysis import ImagineAnalysis
from tracer.models.trace import Trace
from tracer.tests.test_imagine_analysis_api import IMAGINE_ANALYSIS_PATH, result
from tracer.tests.test_imagine_analysis_trace_scope import (
    imagine_scope as imagine_scope,
)


@pytest.fixture
def cache_runtime(monkeypatch):
    dispatch = Mock()
    monkeypatch.setattr("tfc.temporal.imagine.client.start_workflow_sync", dispatch)
    # Preserve the real ORM/thread handoff without closing pytest's transaction.
    monkeypatch.setattr("channels.db.database_sync_to_async", sync_to_async)
    info = SimpleNamespace(workflow_id="unrelated-workflow")
    monkeypatch.setattr("tfc.temporal.imagine.activities.activity.info", lambda: info)
    return SimpleNamespace(dispatch=dispatch, info=info)


@pytest.fixture
def shared_trace_reader(imagine_scope, monkeypatch):
    scope = imagine_scope
    foreign_project = scope.foreign_traces["other_workspace"].project
    spans = [
        SimpleNamespace(
            trace_id=str(scope.local_trace.id),
            project_id=str(project.id),
            name=f"{label}-root-span",
            observation_type="llm",
            latency_ms=12,
            total_tokens=7,
            model="fixture-model",
            status="OK",
            input=f"{label}-input",
            output=f"{label}-output",
        )
        for label, project in (("local", scope.project), ("sibling", foreign_project))
    ]
    assert spans[0].trace_id == spans[1].trace_id, (
        "SETUP_AQ-XWS-W3D ClickHouse spans share the local PostgreSQL trace ID"
    )
    assert (
        scope.local_trace.project_id == scope.project.id
        and foreign_project.id != scope.project.id
        and foreign_project.workspace_id != scope.project.workspace_id
        and foreign_project.organization_id == scope.organization.id
    ), "SETUP_AQ-XWS-W3D sibling-project spans cross workspace within the organization"

    def list_by_trace(trace_id, *, project_id=None):
        return [
            span
            for span in spans
            if span.trace_id == trace_id
            and (project_id is None or span.project_id == project_id)
        ]

    reader = Mock()
    reader.list_by_trace.side_effect = list_by_trace
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader", Mock(return_value=reader)
    )
    return SimpleNamespace(
        reader=reader,
        local=spans[0],
        foreign=spans[1],
        # Reconstruct a historical artifact, not a result of today's scoped fetch.
        legacy_content=f"Historical summary derived from {spans[1].output}",
    )


def _analysis(scope, content, workflow_kind="legacy", trace_id=None):
    analysis_id = uuid.uuid4()
    workflow_id = {
        "legacy": f"imagine-analysis-{analysis_id}",
        "marked": f"imagine-pb1-{analysis_id}",
        "null": None,
        "empty": "",
    }[workflow_kind]
    row = ImagineAnalysis.no_workspace_objects.create(
        id=analysis_id,
        saved_view=scope.saved_view,
        project=scope.project,
        organization=scope.organization,
        widget_id="summary",
        trace_id=str(trace_id or scope.local_trace.id),
        prompt="Historical prompt",
        content=content,
        status="completed",
        error="historical diagnostic",
        workflow_id=workflow_id,
    )
    assert ImagineAnalysis.no_workspace_objects.filter(pk=row.id).exists(), (
        "SETUP_AQ-XWS-W3D historical analysis exists before invoking real guards"
    )
    return row


def _request(client, scope, method, trace_id=None):
    data = {
        "saved_view_id": str(scope.saved_view.id),
        "trace_id": str(trace_id or scope.local_trace.id),
    }
    if method == "post":
        data.update(
            project_id=str(scope.project.id),
            widgets=[{"widget_id": "summary", "prompt": "Fresh scoped summary"}],
        )
        return client.post(IMAGINE_ANALYSIS_PATH, data, format="json")
    return client.get(IMAGINE_ANALYSIS_PATH, data)


@pytest.mark.django_db
@pytest.mark.parametrize("workflow_kind", ["legacy", "null", "empty"])
def test_cached_post_redispatches_legacy_shared_trace_summary(
    auth_client, imagine_scope, shared_trace_reader, cache_runtime, workflow_kind
):
    row = _analysis(
        imagine_scope, shared_trace_reader.legacy_content, workflow_kind=workflow_kind
    )
    response = _request(auth_client, imagine_scope, "post")
    row.refresh_from_db()
    observed = (
        response.status_code,
        result(response).get("analyses"),
        cache_runtime.dispatch.call_count,
        row.content,
        row.error,
        row.status,
        row.prompt,
        row.workflow_id,
    )
    assert observed == (
        200,
        [{"id": str(row.id), "widget_id": "summary", "status": "running"}],
        1,
        None,
        None,
        "running",
        "Fresh scoped summary",
        f"imagine-pb1-{row.id}",
    ), "AQ-XWS-W3D: cached POST must discard legacy shared-trace content and redispatch"


@pytest.mark.django_db
@pytest.mark.parametrize("workflow_kind", ["legacy", "null", "empty"])
def test_poll_hides_legacy_shared_trace_summary(
    auth_client, imagine_scope, shared_trace_reader, cache_runtime, workflow_kind
):
    row = _analysis(
        imagine_scope, shared_trace_reader.legacy_content, workflow_kind=workflow_kind
    )
    response = _request(auth_client, imagine_scope, "get")
    assert (
        response.status_code,
        result(response).get("analyses"),
        cache_runtime.dispatch.call_count,
    ) == (
        200,
        [
            {
                "id": str(row.id),
                "widget_id": "summary",
                "status": "pending",
                "content": None,
                "error": None,
            }
        ],
        0,
    ), "AQ-XWS-W3D: polling must return pending without legacy shared-trace content"
    row.refresh_from_db()
    assert (row.status, row.content) == (
        "completed",
        shared_trace_reader.legacy_content,
    ), "AQ-XWS-W3D: polling must remain read-only while suppressing legacy content"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "execution", ["legacy", "other_marked", "legacy_after_redispatch", "empty"]
)
def test_save_requires_marked_persisted_executing_workflow(
    imagine_scope, shared_trace_reader, cache_runtime, execution
):
    row = _analysis(
        imagine_scope,
        "previous content",
        workflow_kind="legacy" if execution == "legacy" else "marked",
    )
    row.status = "running"
    row.save(update_fields=["status", "updated_at"])
    workflow_id = {
        "legacy": row.workflow_id,
        "other_marked": f"imagine-pb1-{uuid.uuid4()}",
        "legacy_after_redispatch": f"imagine-analysis-{row.id}",
        "empty": "",
    }[execution]
    cache_runtime.info.workflow_id = workflow_id
    input = SaveResultInput(
        analysis_id=str(row.id),
        content=shared_trace_reader.legacy_content,
        project_id=str(imagine_scope.project.id),
        org_id=str(imagine_scope.organization.id),
    )
    returned = async_to_sync(save_analysis_result)(input)
    row.refresh_from_db()
    assert (returned, row.content, row.status, row.error, row.workflow_id) == (
        None,
        "previous content",
        "running",
        "historical diagnostic",
        f"imagine-analysis-{row.id}"
        if execution == "legacy"
        else f"imagine-pb1-{row.id}",
    ), (
        "AQ-XWS-W3D: saves require both the binding marker and the executing workflow match"
    )


def test_starter_mints_binding_version_without_changing_dispatch_options(cache_runtime):
    analysis_id, trace_id, org_id, project_id = (str(uuid.uuid4()) for _ in range(4))
    workflow_id = start_imagine_analysis(
        analysis_id=analysis_id,
        trace_id=trace_id,
        org_id=org_id,
        project_id=project_id,
        prompt="Fresh scoped summary",
    )
    assert cache_runtime.dispatch.call_count == 1, (
        "SETUP_AQ-XWS-W3D bound starter reaches the external Temporal boundary"
    )
    dispatched = cache_runtime.dispatch.call_args
    input = dispatched.args[1]
    assert (
        workflow_id,
        dispatched.args[0],
        dispatched.kwargs,
        input.analysis_id,
        input.trace_id,
        input.org_id,
        input.project_id,
    ) == (
        f"imagine-pb1-{analysis_id}",
        ImagineAnalysisWorkflow,
        {
            "workflow_id": f"imagine-pb1-{analysis_id}",
            "task_queue": "tasks_xl",
            "cancel_existing": False,
        },
        analysis_id,
        trace_id,
        org_id,
        project_id,
    ), (
        "AQ-XWS-W3D: starter must mint the binding-version ID without changing dispatch options"
    )


@pytest.mark.parametrize("missing_field", ["project_id", "org_id"])
@pytest.mark.parametrize("binding", [None, ""])
def test_starter_does_not_mark_an_unbound_run(cache_runtime, missing_field, binding):
    kwargs = {
        "analysis_id": str(uuid.uuid4()),
        "trace_id": str(uuid.uuid4()),
        "org_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "prompt": "Fresh scoped summary",
    }
    kwargs[missing_field] = binding
    refusal = None
    try:
        start_imagine_analysis(**kwargs)
    except ValueError as exc:
        refusal = str(exc)
    assert (refusal, cache_runtime.dispatch.call_count) == (
        "Imagine project binding missing.",
        0,
    ), (
        "AQ-XWS-W3D: starter must refuse missing bindings before minting trusted provenance"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("save_status", ["completed", "failed"])
def test_matching_marked_save_and_same_scope_cache_remain_available(
    auth_client, imagine_scope, shared_trace_reader, cache_runtime, save_status
):
    response = _request(auth_client, imagine_scope, "post")
    assert response.status_code == 200 and cache_runtime.dispatch.call_count == 1, (
        "SETUP_AQ-XWS-W3D local trace passes admission and dispatches a real starter"
    )
    row = ImagineAnalysis.no_workspace_objects.get(
        saved_view=imagine_scope.saved_view,
        trace_id=str(imagine_scope.local_trace.id),
    )
    dispatched = cache_runtime.dispatch.call_args.args[1]
    context = async_to_sync(fetch_trace_data)(
        FetchTraceInput(
            trace_id=dispatched.trace_id,
            org_id=dispatched.org_id,
            project_id=dispatched.project_id,
        )
    )
    assert (
        shared_trace_reader.local.output in context
        and shared_trace_reader.foreign.output not in context
        and shared_trace_reader.reader.list_by_trace.call_args_list
        == [
            call(
                str(imagine_scope.local_trace.id),
                project_id=str(imagine_scope.project.id),
            )
        ]
    ), "AQ-XWS-W3D: same-scope fetch must exclude sibling spans sharing the trace ID"
    cache_runtime.info.workflow_id = row.workflow_id
    content = context if save_status == "completed" else ""
    error = None if save_status == "completed" else "LLM analysis failed after retries"
    async_to_sync(save_analysis_result)(
        SaveResultInput(
            analysis_id=str(row.id),
            content=content,
            status=save_status,
            error=error,
            project_id=dispatched.project_id,
            org_id=dispatched.org_id,
        )
    )
    row.refresh_from_db()
    assert (row.content, row.status, row.error) == (content, save_status, error), (
        "AQ-XWS-W3D: matching marked workflow must persist same-scope success and failure"
    )
    poll = _request(auth_client, imagine_scope, "get")
    assert (poll.status_code, result(poll)["analyses"]) == (
        200,
        [
            {
                "id": str(row.id),
                "widget_id": "summary",
                "status": save_status,
                "content": content,
                "error": error,
            }
        ],
    ), (
        "AQ-XWS-W3D: polling must retain the response contract for marked same-scope results"
    )
    if save_status == "completed":
        cached = _request(auth_client, imagine_scope, "post")
        assert (
            cached.status_code,
            result(cached)["analyses"],
            cache_runtime.dispatch.call_count,
        ) == (
            200,
            [
                {
                    "id": str(row.id),
                    "widget_id": "summary",
                    "status": "completed",
                    "content": content,
                }
            ],
            1,
        ), "AQ-XWS-W3D: marked same-scope cached POST must not redispatch"


@pytest.mark.django_db
def test_fast_save_observes_persisted_marker_and_is_not_overwritten(
    auth_client, imagine_scope, cache_runtime
):
    observed_at_dispatch = []

    def save_before_dispatch_returns(_workflow, input, **options):
        row = ImagineAnalysis.no_workspace_objects.get(pk=input.analysis_id)
        observed_at_dispatch.append(
            (row.workflow_id, row.status, row.content, row.error)
        )
        cache_runtime.info.workflow_id = options["workflow_id"]
        async_to_sync(save_analysis_result)(
            SaveResultInput(
                analysis_id=input.analysis_id,
                content="fast scoped result",
                project_id=input.project_id,
                org_id=input.org_id,
            )
        )

    cache_runtime.dispatch.side_effect = save_before_dispatch_returns
    response = _request(auth_client, imagine_scope, "post")
    row = ImagineAnalysis.no_workspace_objects.get(
        saved_view=imagine_scope.saved_view,
        trace_id=str(imagine_scope.local_trace.id),
    )
    assert (
        response.status_code,
        observed_at_dispatch,
        row.workflow_id,
        row.status,
        row.content,
    ) == (
        200,
        [(f"imagine-pb1-{row.id}", "pending", None, None)],
        f"imagine-pb1-{row.id}",
        "completed",
        "fast scoped result",
    ), (
        "AQ-XWS-W3D: dispatch must persist provenance before a fast save and preserve completion"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["post", "get"])
@pytest.mark.parametrize("foreign_scope", ["other_workspace", "other_project"])
def test_foreign_and_nonexistent_traces_keep_identical_refusal(
    auth_client, imagine_scope, cache_runtime, method, foreign_scope
):
    foreign = imagine_scope.foreign_traces[foreign_scope]
    missing = uuid.uuid4()
    row = _analysis(imagine_scope, "foreign cached summary", trace_id=foreign.id)
    assert Trace.no_workspace_objects.filter(pk=foreign.id).exists(), (
        "SETUP_AQ-XWS-W3D foreign trace exists"
    )
    assert not Trace.no_workspace_objects.filter(pk=missing).exists(), (
        "SETUP_AQ-XWS-W3D nonexistent trace is absent"
    )
    responses = [
        _request(auth_client, imagine_scope, method, trace_id)
        for trace_id in (foreign.id, missing)
    ]
    observed = [(response.status_code, response.json()) for response in responses]
    row.refresh_from_db()
    assert (
        observed[0] == observed[1]
        and observed[0][0] == 404
        and observed[0][1]["code"] == "not_found"
        and "Trace not found" in str(observed[0][1])
        and cache_runtime.dispatch.call_count == 0
        and row.content == "foreign cached summary"
        and row.status == "completed"
    ), (
        "AQ-XWS-W3D: foreign and nonexistent traces must keep identical not-found envelopes"
    )
