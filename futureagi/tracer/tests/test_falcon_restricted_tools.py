"""Exercise the restricted executor and bound tools independently of worker readiness."""

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
from tracer.services.falcon_execution_scope import capture_background_execution_scope
from tracer.tests._ch_seed import delete_ch_spans, seed_ch_spans
from tracer.tests._falcon_analysis import (
    finding_params,
    read_call,
    score_params,
    scripted_tool_stream,
)
from tracer.tests._falcon_hold import HOLD_CODE, no_background_work
from tracer.tests.test_falcon_execution_scope import (
    make_credentialed_attempt,
    revalidate,
)

pytestmark = [pytest.mark.requires_ee, pytest.mark.django_db]


@pytest.fixture
def bound_pair(observe_project, user, monkeypatch):
    project = observe_project
    foreground, attempt, _ = make_credentialed_attempt(project, user, monkeypatch)
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
        scope = capture_background_execution_scope(foreground, attempt=attempt)
        yield revalidate(scope, attempt), sibling
        assert (list(analyses.values()), list(details.values())) == before
        summary.assert_not_called()
    finally:
        delete_ch_spans([span_id for span_id, _ in roots.values()])


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("include_reader", [True, False], ids=["reader-only", "empty"])
def test_restricted_loop_respects_tool_list(bound_pair, monkeypatch, include_reader):
    from ee.falcon_ai.agent import AgentLoop

    bound, _ = bound_pair
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

    result = async_to_sync(agent.run)(
        user_message="Read the assigned trace",
        history_messages=[],
        send_callback=AsyncMock(),
        system_prompt_override="Use the assigned tools.",
        tools_override=tool_list,
        raise_on_stream_error=True,
    )

    assert result["finish_reason"] == "stop" and provider.call_count == 3
    for turn in provider.call_args_list:
        exposed = [schema["function"]["name"] for schema in (turn.args[1] or [])]
        assert exposed == ([allowed.name] if include_reader else [])
    read, denied = result["tool_calls"]
    assert read["tool_name"] == allowed.name
    if include_reader:
        assert (
            read["status"] == "completed" and f"root-{trace_id}" in read["result_full"]
        )
    else:
        assert (
            read["status"] == "error"
            and f"Tool '{allowed.name}' not found" in read["result_full"]
        )
    assert (denied["tool_name"], denied["status"]) == (forbidden.name, "error")
    reason = f"Tool '{forbidden.name}' not found"
    assert reason in denied["result_full"]
    final_messages = provider.call_args.args[0]
    assert any(
        msg.get("tool_call_id") == "call-1" and reason in msg["content"]
        for msg in final_messages
        if msg["role"] == "tool"
    )
    invoked.assert_not_called()
    load_external_tools.assert_not_awaited()


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
    bound, sibling = bound_pair
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
    assert denied.is_error and denied.error_code == "NOT_FOUND" and denied.data is None
    assert f"Trace with ID `{trace_id}`" in denied.content
    reader.assert_not_called()


@pytest.mark.parametrize("name", ["submit_trace_scores", "submit_trace_finding"])
def test_bound_write_rejects_sibling_analysis_handle(bound_pair, name):
    bound, sibling = bound_pair
    trace_id = bound.assigned_trace_id
    params = (
        score_params(trace_id)
        if name == "submit_trace_scores"
        else (finding_params(trace_id) | {"location_spans": [f"root-{trace_id}"]})
    )
    denied = registry.get(name).run(params | {"analysis_id": str(sibling.pk)}, bound)
    assert denied.is_error and denied.error_code == "NOT_FOUND" and denied.data is None
    assert f"Analysis with ID `{sibling.pk}`" in denied.content


@pytest.mark.parametrize("name", ["read_trace_span", "submit_trace_finding"])
def test_bound_tool_rejects_sibling_span_on_assigned_trace(bound_pair, name):
    bound, sibling = bound_pair
    trace_id = bound.assigned_trace_id
    span_id = f"root-{sibling.trace_id}"
    if name == "read_trace_span":
        params = read_call(trace_id, span_id)[1]
        code, reason = "NOT_FOUND", f"Span `{span_id}` not found in trace `{trace_id}`"
    else:
        params = finding_params(trace_id) | {
            "location_spans": [f"root-{trace_id}", span_id]
        }
        code, reason = "VALIDATION_ERROR", "span outside the assigned trace"
    denied = registry.get(name).run(params, bound)
    assert denied.is_error and denied.error_code == code and denied.data is None
    assert reason in denied.content


def test_held_empty_list_refuses_before_project_trace_discovery(observe_project):
    project = observe_project
    ctx = ToolContext(
        project.workspace.created_by, project.organization, project.workspace
    )
    tool = registry.get("analyze_project_traces")
    with no_background_work(), CaptureQueriesContext(connection) as queries:
        denied = tool.run({"project_id": str(project.pk), "trace_ids": []}, ctx)
    assert denied.is_error and denied.error_code == HOLD_CODE and denied.data is None
    # Authorizing the project needs SQL. Listing its analyzed traces does not belong before the hold.
    sql = [query["sql"] for query in queries]
    assert any(project._meta.db_table in statement for statement in sql)
    assert not any(TraceErrorAnalysis._meta.db_table in statement for statement in sql)
