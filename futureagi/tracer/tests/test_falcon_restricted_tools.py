"""Exercise the restricted executor and bound tools independently of worker readiness."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from ai_tools.base import ToolContext
from ai_tools.registry import registry
from ai_tools.tools.tracing.explore_trace import ExploreTraceTool
from tracer.models.trace_error_analysis import TraceErrorAnalysis, TraceErrorDetail
from tracer.services.clickhouse import v2
from tracer.services.falcon_background_analysis import BackgroundAnalysisUnavailable
from tracer.tests._ch_seed import delete_ch_spans, seed_ch_spans
from tracer.tests._falcon_analysis import (
    finding_params,
    read_call,
    score_params,
    scripted_tool_stream,
)
from tracer.tests._falcon_hold import (
    HOLD_CODE,
    HOLD_MESSAGE,
    assert_hold,
    no_background_work,
)
from tracer.tests.test_falcon_execution_scope import (
    bound_context,
    make_credentialed_attempt,
)

pytestmark = [pytest.mark.requires_ee, pytest.mark.django_db]


@pytest.fixture
def bound_pair(observe_project, user, monkeypatch):
    project = observe_project
    foreground, attempt, scope = make_credentialed_attempt(project, user)
    sibling = TraceErrorAnalysis.objects.create(
        project=project, trace_id=uuid4(), insights="Keep this analysis"
    )
    TraceErrorDetail.objects.create(
        analysis=sibling,
        error_id="E001",
        category="Evidence",
        description="Keep this finding",
    )
    trace_ids = [str(attempt.trace_id), str(sibling.trace_id)]
    roots = {tid: (f"root-{tid}", str(project.pk)) for tid in trace_ids}
    analyses = TraceErrorAnalysis.objects.filter(project=project).order_by("pk")
    details = TraceErrorDetail.objects.filter(analysis__project=project).order_by("pk")
    before = list(analyses.values()), list(details.values())
    # Even a regression in an explorer must not reach a real summary provider.
    summary = Mock(side_effect=AssertionError("Unexpected nested model call"))
    monkeypatch.setattr("litellm.completion", summary)
    try:
        seed_ch_spans(
            {
                "id": span_id,
                "name": "root",
                "trace_id": tid,
                "project_id": project_id,
                "observation_type": "llm",
                "start_time": timezone.now(),
            }
            for tid, (span_id, project_id) in roots.items()
        )
        # A missing sibling would give a false positive for a trace or span rejection.
        with v2.get_reader() as reader:
            found = reader.root_ids_by_trace_ids(
                trace_ids, project_ids=[str(project.pk)]
            )
            assert found == roots
        yield bound_context(foreground, attempt, scope), sibling, foreground
        assert (list(analyses.values()), list(details.values())) == before
        summary.assert_not_called()
    finally:
        delete_ch_spans([span_id for span_id, _ in roots.values()])


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("include_reader", [True, False], ids=["reader-only", "empty"])
def test_background_loop_refuses_before_tool_list(
    bound_pair, monkeypatch, include_reader
):
    from ee.falcon_ai.agent import AgentLoop

    bound, _, _ = bound_pair
    trace_id = bound.assigned_trace_id
    allowed = registry.get("read_trace_span")
    forbidden = registry.get("analyze_project_traces")
    assert allowed is not None and forbidden is not None
    invoked = Mock(side_effect=AssertionError("Unlisted registry tool invoked"))
    monkeypatch.setattr(forbidden, "run", invoked)
    monkeypatch.setattr(forbidden, "execute", invoked)
    agent = AgentLoop(
        bound, SimpleNamespace(title="Restricted tools", context_summary="")
    )
    provider = Mock(
        side_effect=scripted_tool_stream(
            read_call(trace_id, f"root-{trace_id}"),
            (forbidden.name, {"project_id": str(bound.project.pk)}),
        )
    )
    monkeypatch.setattr(agent.llm_client, "stream_completion", provider)
    load_external_tools = AsyncMock(return_value=[])
    monkeypatch.setattr(agent, "_load_mcp_tools", load_external_tools)
    tool_list = [allowed] if include_reader else []

    args = {
        "user_message": "Read the assigned trace",
        "history_messages": [],
        "send_callback": AsyncMock(),
        "system_prompt_override": "Use the assigned tools.",
        "tools_override": tool_list,
        "raise_on_stream_error": True,
    }
    with pytest.raises(BackgroundAnalysisUnavailable) as denied_background:
        async_to_sync(agent.run)(**args)
    assert denied_background.value.code == HOLD_CODE
    provider.assert_not_called()
    load_external_tools.assert_not_awaited()
    invoked.assert_not_called()


@pytest.mark.parametrize(
    "name",
    [
        "explore_trace",
        "explore_trace_legacy",
        "read_trace_span",
        "submit_trace_scores",
        "submit_trace_finding",
    ],
)
def test_bound_tool_rejects_sibling_trace_before_reader(bound_pair, monkeypatch, name):
    bound, sibling, _ = bound_pair
    trace_id = str(sibling.trace_id)
    span_id = f"root-{trace_id}"
    params = {
        "read_trace_span": read_call(trace_id, span_id)[1],
        "submit_trace_scores": score_params(trace_id),
        "submit_trace_finding": finding_params(trace_id)
        | {"location_spans": [span_id]},
    }.get(name, {"trace_id": trace_id})
    if name == "explore_trace":
        # The worker aliases the scoped explorer; the global short name is a different tool.
        tool = ExploreTraceTool()
        tool.name = name
    else:
        tool = registry.get(name)
    assert tool is not None
    reader = Mock(side_effect=AssertionError("Sibling trace requested"))
    monkeypatch.setattr(v2, "get_reader", reader)
    denied = tool.run(params, bound)
    assert denied.is_error and denied.error_code == HOLD_CODE and denied.data is None
    assert trace_id not in denied.content
    reader.assert_not_called()


@pytest.mark.parametrize("name", ["submit_trace_scores", "submit_trace_finding"])
def test_bound_write_rejects_sibling_analysis_handle(bound_pair, name):
    bound, sibling, _ = bound_pair
    trace_id = bound.assigned_trace_id
    params = (
        score_params(trace_id)
        if name == "submit_trace_scores"
        else (finding_params(trace_id) | {"location_spans": [f"root-{trace_id}"]})
    )
    denied = registry.get(name).run(params | {"analysis_id": str(sibling.pk)}, bound)
    assert denied.is_error and denied.error_code == HOLD_CODE and denied.data is None
    assert str(sibling.pk) not in denied.content


@pytest.mark.parametrize("name", ["read_trace_span", "submit_trace_finding"])
def test_bound_tool_rejects_sibling_span_on_assigned_trace(bound_pair, name):
    bound, sibling, _ = bound_pair
    trace_id = bound.assigned_trace_id
    span_id = f"root-{sibling.trace_id}"
    if name == "read_trace_span":
        params = read_call(trace_id, span_id)[1]
    else:
        params = finding_params(trace_id) | {
            "location_spans": [f"root-{trace_id}", span_id]
        }
    denied = registry.get(name).run(params, bound)
    assert denied.is_error and denied.error_code == HOLD_CODE and denied.data is None
    assert span_id not in denied.content


def test_held_empty_list_refuses_before_project_trace_discovery(observe_project):
    project = observe_project
    ctx = ToolContext(
        project.workspace.created_by, project.organization, project.workspace
    )
    tool = registry.get("analyze_project_traces")
    with no_background_work(), CaptureQueriesContext(connection) as queries:
        denied = tool.run({"project_id": str(project.pk), "trace_ids": []}, ctx)
    assert denied.is_error and denied.error_code == HOLD_CODE and denied.data is None
    assert_hold(denied.error_code, denied.content)
    assert not list(queries)


@pytest.mark.parametrize(
    "marker", ["revoked-capture", "assigned-only", "malformed-scope"]
)
def test_background_markers_refuse_every_executor_entry(
    bound_pair, marker, monkeypatch
):
    from accounts.models.auth_token import AuthToken
    from ai_tools.generated import GeneratedAPITool
    from ee.falcon_ai.agent import AgentLoop
    from ee.falcon_ai.mcp_tools import MCPToolWrapper

    bound, _, _ = bound_pair
    tid = bound.assigned_trace_id
    if marker == "revoked-capture":
        assert (
            AuthToken.objects.filter(pk=bound.auth_scope.token_id).update(
                is_active=False
            )
            == 1
        )
    elif marker == "assigned-only":
        bound = replace(bound, background_execution_scope=None)
    else:
        bound = replace(bound, assigned_trace_id=None, background_execution_scope={})

    native = registry.get("read_trace_span")
    dispatch = Mock(side_effect=AssertionError("Unexpected dispatch"))
    monkeypatch.setattr(native, "execute", dispatch)
    executor = Mock()
    generated = GeneratedAPITool(
        SimpleNamespace(
            name="local_read", description="Read", group="tracing", input_schema={}
        ),
        executor,
    )
    monkeypatch.setattr(generated, "_coerce_params", dispatch)
    mcp = MCPToolWrapper(SimpleNamespace(name="local"), {"name": "read"})
    monkeypatch.setattr(mcp, "_call_mcp_sync", dispatch)
    params = mcp.input_model()
    agent = AgentLoop(bound, SimpleNamespace(title="Unstarted"))
    with no_background_work(tid):
        for result in (
            native.run({}, bound),  # hold precedes input validation
            generated.run({}, bound),
            generated.execute({}, bound),
            mcp.execute(params, bound),
            async_to_sync(mcp.async_execute)(params, bound),
        ):
            assert result.is_error and result.data is None
            assert_hold(result.error_code, result.content)
        with pytest.raises(BackgroundAnalysisUnavailable) as denied:
            async_to_sync(agent.run)("Read a trace", [], AsyncMock())
        assert denied.value.code == HOLD_CODE
    assert_hold(denied.value.code, str(denied.value))
    executor.execute_sync.assert_not_called()
    dispatch.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_interactive_tool_event_reports_refusal_without_promising_results(
    observe_project, user, monkeypatch
):
    from ee.falcon_ai.agent import AgentLoop

    project = observe_project
    tool = registry.get("analyze_project_traces")
    ctx = ToolContext(user, project.organization, project.workspace)
    agent = AgentLoop(ctx, SimpleNamespace(title="Trace request", context_summary=""))
    callback = AsyncMock()
    enqueue = Mock(side_effect=AssertionError("Held producer enqueued a job"))
    monkeypatch.setattr(
        "tracer.tasks.error_analysis.analyze_traces_on_demand.delay", enqueue
    )
    monkeypatch.setattr(
        agent.llm_client,
        "stream_completion",
        scripted_tool_stream((tool.name, {"project_id": str(project.pk)})),
    )
    async_to_sync(agent.run)(
        "Analyze the project",
        [],
        callback,
        tools_override=[tool],
        system_prompt_override="Use the analysis tool.",
        raise_on_stream_error=True,
    )
    [event] = [
        call.args[0]["data"]
        for call in callback.await_args_list
        if call.args[0]["type"] == "tool_call_result"
    ]
    assert event["tool_name"] == tool.name and event["status"] == "error"
    assert (
        HOLD_MESSAGE in event["result_summary"] and HOLD_MESSAGE in event["result_full"]
    )
    enqueue.assert_not_called()
