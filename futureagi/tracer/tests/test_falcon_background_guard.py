"""The readiness hold is a negative contract, not acceptance of enabled analysis."""

import uuid
from unittest.mock import Mock

import pytest
from temporalio.exceptions import ApplicationError

from ai_tools.base import ToolContext
from tracer.models.trace_error_analysis import TraceErrorAnalysis
from tracer.tasks.error_analysis import (
    _falcon_analyze_single_trace,
    analyze_traces_on_demand,
)


@pytest.mark.requires_ee
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("entry", ["tool", "queued_activity", "single_trace"])
def test_unready_background_analysis_fails_before_spend_or_dispatch(
    project, monkeypatch, entry
):
    from ai_tools.registry import registry
    from ee.usage.deployment import DeploymentMode

    trace_id, project_id = str(uuid.uuid4()), str(project.id)
    monkeypatch.setattr(DeploymentMode, "is_oss", staticmethod(lambda: False))
    sentinels = []
    for target in (
        "tracer.services.clickhouse.v2.get_reader",
        "tracer.queries.deep_analysis_state.set_running",
        "tracer.queries.deep_analysis_state.set_failed",
        "tracer.queries.deep_analysis_state.clear",
        "tracer.tasks.error_analysis.analyze_traces_on_demand.delay",
        "ee.falcon_ai.agent.AgentLoop.__init__",
        "ee.falcon_ai.llm_client.FalconLLMClient.stream_completion",
        "litellm.completion",
        "ee.usage.services.emitter.emit",
        "tracer.queries.error_analysis.TraceErrorAnalysisDB.ingest_trace_error_embeddings",
        "tracer.tasks.error_analysis.cluster_project_errors.delay",
    ):
        call = Mock(name=target, side_effect=AssertionError(f"Unexpected call to {target}"))
        monkeypatch.setattr(target, call)
        sentinels.append(call)

    if entry == "tool":
        ctx = ToolContext(
            user=project.workspace.created_by,
            organization=project.organization,
            workspace=project.workspace,
        )
        result = registry.get("analyze_project_traces").run(
            {"project_id": project_id, "trace_ids": [trace_id]}, ctx
        )
        assert result.is_error
        code, message = result.error_code, result.content
    elif entry == "queued_activity":
        # Previously queued messages must fail in Temporal, not complete with zero errors.
        with pytest.raises(ApplicationError) as denied:
            analyze_traces_on_demand(project_id, [trace_id])
        assert denied.value.non_retryable
        code, message = denied.value.type, str(denied.value)
    else:
        result = _falcon_analyze_single_trace(trace_id, project_id)
        assert result["success"] is False
        assert result["trace_id"] == trace_id
        code, message = result["error_code"], result["error"]

    assert code == "FALCON_BACKGROUND_ACCOUNTING_UNAVAILABLE"
    assert "allowance" in message and "usage recording" in message
    assert "nested trace explorer" in message
    if entry != "tool":
        assert "without an initiating user" in message
    for call in sentinels:
        call.assert_not_called()
    assert not TraceErrorAnalysis.objects.filter(trace_id=trace_id, project=project).exists()
