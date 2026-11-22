"""Background trace analysis must use the real Falcon agent constructor."""

import uuid
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from tracer.models.trace_error_analysis import TraceErrorAnalysis
from tracer.tasks.error_analysis import _falcon_analyze_single_trace
from tracer.tests._falcon_analysis import (
    install_reader,
    install_tool_stream,
    read_call,
    tool_chunk,
)

pytestmark = pytest.mark.requires_ee


@pytest.fixture
def global_analysis_skill(db):
    from ee.falcon_ai.builtin_skills_loader import load_builtin_skills
    from ee.falcon_ai.models import Skill

    spec = next(s for s in load_builtin_skills() if s["slug"] == "analyze-trace-errors")
    skill, _ = Skill.no_workspace_objects.update_or_create(
        organization=None,
        slug=spec["slug"],
        defaults={**spec, "workspace": None, "is_builtin": True, "is_active": True},
    )
    return skill


@pytest.mark.django_db(transaction=True)
def test_falcon_analysis_runs_agent_and_records_clean_result(
    project, global_analysis_skill, monkeypatch
):
    from ee.falcon_ai.agent import AgentLoop
    from ee.falcon_ai.llm_client import FalconLLMClient
    from ee.falcon_ai.models import Conversation, Skill
    from ee.usage.deployment import DeploymentMode

    trace_id = str(uuid.uuid4())
    install_reader(monkeypatch, project, trace_id)
    Skill.objects.create(
        organization=project.organization,
        workspace=project.workspace,
        name="Analyze trace errors",
        slug="analyze-trace-errors",
        instructions="Tenant-only instructions.",
    )
    streamed_messages = []
    advertised_tools = []

    async def stream_completion(self, messages, tools=None):
        streamed_messages.append(list(messages))
        advertised_tools.append({tool["function"]["name"] for tool in tools or []})
        if len(streamed_messages) == 1:
            yield tool_chunk(0, *read_call(trace_id))
            return
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
    discover = AsyncMock(return_value=[])
    monkeypatch.setattr(AgentLoop, "_load_mcp_tools", discover)
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
    assert len(streamed_messages) == 2
    discover.assert_not_called()
    assert advertised_tools == 2 * [
        {
            "explore_trace",
            "explore_trace_legacy",
            "read_trace_span",
            "submit_trace_finding",
            "submit_trace_scores",
        }
    ]
    assert global_analysis_skill.instructions in streamed_messages[0][0]["content"]
    assert "Tenant-only instructions." not in streamed_messages[0][0]["content"]
    assert "Own evidence" in streamed_messages[1][-1]["content"]
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
    clear.assert_called_once_with(trace_id, project_id=str(project.id))
    failed.assert_not_called()
    clickhouse.return_value.insert.assert_called_once()
    logged_result = clickhouse.return_value.insert.call_args.args[1][0]
    assert logged_result["response"] == "No errors found."


@pytest.fixture
def falcon_boundaries(global_analysis_skill, monkeypatch):
    """Keep the real agent/ORM; stub only external services and marker writes."""
    from ee.falcon_ai.agent import AgentLoop
    from ee.usage.deployment import DeploymentMode

    monkeypatch.setattr(DeploymentMode, "is_oss", staticmethod(lambda: False))
    monkeypatch.setattr(AgentLoop, "_load_mcp_tools", AsyncMock(return_value=[]))
    clear = Mock()
    failed = Mock()
    clickhouse = Mock()
    monkeypatch.setattr("tracer.queries.deep_analysis_state.clear", clear)
    monkeypatch.setattr("tracer.queries.deep_analysis_state.set_failed", failed)
    monkeypatch.setattr(
        "tracer.services.clickhouse.client.ClickHouseClient", clickhouse
    )
    return clear, failed, clickhouse


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("same_organization", [False, True])
@pytest.mark.parametrize("existing_analysis", [False, True])
def test_falcon_analysis_scopes_reused_trace_id_to_project(
    project, falcon_boundaries, same_organization, existing_analysis, monkeypatch
):
    from accounts.models.organization import Organization
    from accounts.models.user import User
    from accounts.models.workspace import Workspace
    from tracer.models.project import Project

    trace_id = str(uuid.uuid4())
    own_analysis = None
    if existing_analysis:
        own_analysis = TraceErrorAnalysis.objects.create(
            trace_id=trace_id, project=project, overall_score=None
        )

    other_org = project.organization
    other_workspace = project.workspace
    if not same_organization:
        other_org = Organization.objects.create(name="Other organization")
        other_user = User.objects.create_user(
            email=f"other-{uuid.uuid4().hex}@futureagi.com",
            password="test-password",
            organization=other_org,
        )
        other_workspace = Workspace.objects.create(
            name="Other workspace", organization=other_org, created_by=other_user
        )
    other_project = Project.objects.create(
        name="Other project",
        organization=other_org,
        workspace=other_workspace,
        model_type=project.model_type,
        trace_type=project.trace_type,
    )
    # Check that the worker selects the requesting project's analysis.
    other_analysis = TraceErrorAnalysis.objects.create(
        trace_id=trace_id,
        project=other_project,
        overall_score=None,
        insights="Keep this organization's analysis unchanged.",
    )
    other_before = (
        TraceErrorAnalysis.objects.filter(pk=other_analysis.pk).values().get()
    )

    install_reader(monkeypatch, project, trace_id)
    install_tool_stream(monkeypatch, read_call(trace_id))
    result = _falcon_analyze_single_trace(trace_id, str(project.id))

    assert (
        TraceErrorAnalysis.objects.filter(pk=other_analysis.pk).values().get()
        == other_before
    )
    analysis = TraceErrorAnalysis.objects.get(trace_id=trace_id, project=project)
    clear, failed, clickhouse = falcon_boundaries
    if existing_analysis:
        assert analysis.pk == own_analysis.pk
        assert analysis.overall_score is None
        assert result["success"] is False
        assert "did not submit an update" in result["error"]
        failed.assert_called_once_with(trace_id, project_id=str(project.id))
        clear.assert_not_called()
        clickhouse.return_value.insert.assert_not_called()
        return
    assert result == {"success": True, "trace_id": trace_id, "error_count": 0}
    assert analysis.overall_score == 4.0
    assert analysis.insights == "No errors found during analysis."
    clear.assert_called_once_with(trace_id, project_id=str(project.id))
    failed.assert_not_called()
    logged_result = clickhouse.return_value.insert.call_args.args[1][0]
    assert logged_result["project_id"] == str(project.id)
    assert logged_result["organization_id"] == str(project.organization_id)
    assert logged_result["overall_score"] == analysis.overall_score


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("partial_response", [False, True])
def test_falcon_analysis_marks_failed_stream_without_clean_result(
    project, falcon_boundaries, monkeypatch, partial_response
):
    from ee.falcon_ai.llm_client import FalconLLMClient

    trace_id = str(uuid.uuid4())
    stream_calls = []

    async def failed_stream(self, messages, tools=None):
        stream_calls.append(messages)
        if partial_response:
            yield {
                "choices": [
                    {"delta": {"content": "Inspecting trace..."}, "finish_reason": None}
                ]
            }
        raise httpx.RemoteProtocolError("Provider stream interrupted")

    monkeypatch.setattr(FalconLLMClient, "stream_completion", failed_stream)

    result = _falcon_analyze_single_trace(trace_id, str(project.id))

    assert len(stream_calls) == 1
    assert result["success"] is False
    assert result["trace_id"] == trace_id
    assert "Provider stream interrupted" in result["error"]
    assert not TraceErrorAnalysis.objects.filter(trace_id=trace_id).exists()
    clear, failed, clickhouse = falcon_boundaries
    clear.assert_not_called()
    failed.assert_called_once_with(trace_id, project_id=str(project.id))
    clickhouse.return_value.insert.assert_not_called()


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
