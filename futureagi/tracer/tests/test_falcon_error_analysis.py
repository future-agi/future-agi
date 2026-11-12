"""Background trace analysis must use the real Falcon agent constructor."""

import uuid
from unittest.mock import AsyncMock, Mock

import pytest

from tracer.models.trace_error_analysis import TraceErrorAnalysis
from tracer.tasks.error_analysis import _falcon_analyze_single_trace

pytestmark = pytest.mark.requires_ee


@pytest.mark.django_db(transaction=True)
def test_falcon_analysis_runs_agent_and_records_clean_result(project, monkeypatch):
    from ee.falcon_ai.agent import AgentLoop
    from ee.falcon_ai.llm_client import FalconLLMClient
    from ee.falcon_ai.models import Conversation, Skill
    from ee.usage.deployment import DeploymentMode

    trace_id = str(uuid.uuid4())
    instructions = "Inspect the trace and report any errors."
    Skill.objects.create(
        organization=project.organization,
        workspace=project.workspace,
        name="Analyze trace errors",
        slug="analyze-trace-errors",
        instructions=instructions,
    )
    streamed_messages = []

    async def stream_completion(self, messages, tools=None):
        streamed_messages.append(messages)
        yield {
            "choices": [
                {
                    "delta": {"content": "No errors found."},
                    "finish_reason": "stop",
                }
            ],
            "model": "test-model",
        }

    monkeypatch.setattr(DeploymentMode, "is_oss", staticmethod(lambda: False))
    monkeypatch.setattr(FalconLLMClient, "stream_completion", stream_completion)
    monkeypatch.setattr(AgentLoop, "_load_mcp_tools", AsyncMock(return_value=[]))
    clear = Mock()
    failed = Mock()
    monkeypatch.setattr("tracer.queries.deep_analysis_state.clear", clear)
    monkeypatch.setattr("tracer.queries.deep_analysis_state.set_failed", failed)
    clickhouse = Mock()
    monkeypatch.setattr(
        "tracer.services.clickhouse.client.ClickHouseClient", clickhouse
    )

    result = _falcon_analyze_single_trace(trace_id, str(project.id))

    assert result == {"success": True, "trace_id": trace_id, "error_count": 0}
    assert len(streamed_messages) == 1
    assert instructions in streamed_messages[0][0]["content"]
    assert (
        streamed_messages[0][-1]["content"] == f"Analyze trace {trace_id} for errors."
    )
    analysis = TraceErrorAnalysis.objects.get(trace_id=trace_id)
    assert analysis.project_id == project.id
    assert analysis.overall_score == 4.0
    assert analysis.total_errors == 0
    assert analysis.agent_version == "falcon-skill-1.0"
    conversation = Conversation.objects.get(title=f"Auto-analysis: {trace_id[:8]}")
    assert conversation.organization_id == project.organization_id
    assert conversation.workspace_id == project.workspace_id
    assert conversation.metadata == {"hidden": True, "type": "background_analysis"}
    clear.assert_called_once_with(trace_id)
    failed.assert_not_called()
    clickhouse.return_value.insert.assert_called_once()
    logged_result = clickhouse.return_value.insert.call_args.args[1][0]
    assert logged_result["response"] == "No errors found."


def test_falcon_analysis_still_skips_oss_without_creating_agent(monkeypatch):
    from ee.falcon_ai import agent
    from ee.usage.deployment import DeploymentMode

    trace_id = str(uuid.uuid4())
    monkeypatch.setattr(DeploymentMode, "is_oss", staticmethod(lambda: True))
    constructor = Mock()
    monkeypatch.setattr(agent, "AgentLoop", constructor)

    result = _falcon_analyze_single_trace(trace_id, str(uuid.uuid4()))

    assert result == {
        "success": False,
        "trace_id": trace_id,
        "error": "Falcon AI not available (requires EE)",
    }
    constructor.assert_not_called()
