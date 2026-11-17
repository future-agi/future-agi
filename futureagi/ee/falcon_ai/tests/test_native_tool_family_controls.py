"""S2 positive controls: real native bodies through the interactive execution gate.

The complete native directory-family census is context, docs, evaluations,
tracing, users and web. Only external service/LLM clients are replaced; model
queries, access decisions, tool validation and result formatting remain real.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from accounts.models.organization_membership import OrganizationMembership
from accounts.tests import workspace_write_support as write_support
from ai_tools.base import ToolContext
from ai_tools.tools.context.search import SearchTool
from ai_tools.tools.docs.search_docs import SearchDocsTool
from ai_tools.tools.evaluations.evaluate_with_agent import EvaluateWithAgentTool
from ai_tools.tools.tracing.read_trace_span import ReadTraceSpanTool
from ai_tools.tools.users.get_user import GetUserTool
from ai_tools.tools.web.brave_search import BraveSearchTool
from ee.falcon_ai.tests import workspace_gate_support as support
from ee.falcon_ai.tests.test_workspace_scope import execute
from model_hub.models.develop_dataset import Row
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tracer.models.project import Project
from tracer.models.trace import Trace

scope_state = support.scope_state
active_context = support.active_context
licensed_features = support.licensed_features
scope_no_egress = write_support.scope_no_egress
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("scope_no_egress"),
]


def dispatch(context, tool, params):
    assert context.interactive_falcon_socket is True
    result = execute(context, tool, params, use_async=True)
    assert result.error_code != "workspace_access_revoked", result
    assert not result.is_error, result
    return result


@pytest.mark.parametrize("actor", ["member", "owner", "admin"])
def test_context_search_active_interactive_member_succeeds(
    scope_state, active_context, actor
):
    context = active_context
    if actor != "member":
        if actor == "admin":
            OrganizationMembership._base_manager.filter(user=scope_state.owner).update(
                role=OrganizationRoles.ADMIN, level=Level.ADMIN
            )
        context = ToolContext(
            scope_state.owner,
            scope_state.org,
            scope_state.private,
            interactive_falcon_socket=True,
        )
    result = dispatch(
        context,
        SearchTool(),
        {"query": "native-scope-needle", "entity_types": ["datasets"]},
    )
    assert result.data["results"]["datasets"] == [
        {"id": str(scope_state.hidden.pk), "name": scope_state.hidden.name}
    ]
    assert result.data["total"] == 1
    assert str(scope_state.visible.pk) not in str(result.data)


def test_docs_search_active_interactive_member_succeeds(active_context, monkeypatch):
    # This proxy is the external docs-agent RPC boundary, not the native body.
    rpc = Mock(
        return_value=json.dumps(
            {"results": [{"title": "Workspace guide", "snippet": "Active access"}]}
        )
    )
    monkeypatch.setattr("ai_tools.tools.docs.search_docs.call_docs_agent", rpc)
    result = dispatch(active_context, SearchDocsTool(), {"query": "workspace"})
    rpc.assert_called_once_with("search_docs", {"query": "workspace", "limit": 5})
    assert result.data == {"source": "docs-agent"}
    assert "Workspace guide" in result.content
    assert "Active access" in result.content


def test_evaluations_agent_active_interactive_member_succeeds(
    scope_state, active_context, licensed_features, monkeypatch
):
    from ee.agenthub.eval_orchestrator import orchestrator, scout

    row = Row._base_manager.create(dataset=scope_state.hidden, order=0)
    brief = {
        "complexity": "simple",
        "recommended_model": "small",
        "criteria_needs_formalization": False,
        "data_plan": {"sufficient": True, "needs_drill_down": [], "relevant_areas": []},
        "resource_plan": {"use_kb": False, "use_feedback": False, "use_mcp_tools": []},
        "reasoning": "Deterministic provider response for the access control",
    }
    # Keep both agent loops, input summary and real source/resource queries.
    # Replace only the external Turing client; its two responses finish the loop.
    client = SimpleNamespace(
        token_usage={},
        chat_completion=Mock(
            side_effect=[
                json.dumps(brief),
                '<tool_call>{"tool":"submit_result","params":'
                '{"result":"Passed","confidence":1.0,'
                '"explanation":"Active member evaluated the row"}}</tool_call>',
            ]
        ),
    )
    client_factory = Mock(return_value=client)
    monkeypatch.setattr(scout, "TuringClient", client_factory)
    monkeypatch.setattr(orchestrator, "TuringClient", client_factory)
    result = dispatch(
        active_context,
        EvaluateWithAgentTool(),
        {
            "source_id": str(row.pk),
            "input_scope": "dataset_row",
            "criteria": "The row is available for evaluation",
            "choices": ["Passed", "Failed"],
        },
    )
    assert client.chat_completion.call_count == 2
    scout_prompt = client.chat_completion.call_args_list[0].kwargs["messages"][1][
        "content"
    ]
    assert f"DATASET ROW: {row.pk}" in scout_prompt
    assert "Summary unavailable" not in scout_prompt
    assert result.data["result"] == "Passed"
    assert result.data["confidence"] == 1.0
    assert result.data["orchestrator_turns"] == 1
    assert "Active member evaluated the row" in result.content


def test_tracing_span_active_interactive_member_succeeds(active_context, monkeypatch):
    project = Project._base_manager.create(
        name="Native positive trace project",
        organization=active_context.organization,
        workspace=active_context.workspace,
        user=active_context.user,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    trace = Trace._base_manager.create(project=project, name="Native positive trace")
    span = SimpleNamespace(
        id="native-positive-span",
        name="Accessible span",
        observation_type="llm",
        status="success",
        model="test-model",
        latency_ms=1,
        total_tokens=2,
        parent_span_id=None,
        start_time=None,
        input="Authorized trace input",
        output="Authorized trace output",
        metadata={},
        span_events=[],
    )
    # Only ClickHouse transport is replaced; the PostgreSQL trace scope is real.
    reader = MagicMock()
    reader.__enter__.return_value = reader
    reader.list_by_trace.return_value = [span]
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader", Mock(return_value=reader)
    )
    result = dispatch(
        active_context,
        ReadTraceSpanTool(),
        {"trace_id": str(trace.pk), "span_id": span.id},
    )
    reader.list_by_trace.assert_called_once_with(
        str(trace.pk), project_id=str(trace.project_id)
    )
    assert result.data["span_id"] == span.id
    assert result.data["name"] == "Accessible span"
    assert "Authorized trace output" in result.content


def test_users_profile_active_interactive_member_succeeds(active_context):
    result = dispatch(
        active_context, GetUserTool(), {"user_id": str(active_context.user.pk)}
    )
    profile = result.data["user"]
    assert profile["id"] == str(active_context.user.pk)
    assert profile["is_active"] is True
    assert {row["workspace_id"] for row in profile["workspace_memberships"]} == {
        str(active_context.workspace.pk)
    }
    assert {row["organization_id"] for row in profile["organization_memberships"]} == {
        str(active_context.organization.pk)
    }


def test_web_search_active_interactive_member_succeeds(active_context, monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-only-not-a-credential")
    monkeypatch.setenv("BRAVE_SEARCH_USE_LLM_CONTEXT", "false")
    response = Mock()
    response.json.return_value = {
        "web": {
            "results": [
                {
                    "title": "Workspace reference",
                    "url": "https://example.test/workspaces",
                    "description": "Authorized search result",
                }
            ]
        }
    }
    get = Mock(return_value=response)
    monkeypatch.setattr("ai_tools.tools.web.brave_search.requests.get", get)
    result = dispatch(active_context, BraveSearchTool(), {"query": "workspace"})
    get.assert_called_once()
    assert get.call_args.kwargs["params"]["q"] == "workspace"
    response.raise_for_status.assert_called_once()
    assert result.data["source"] == "brave_web_search"
    assert result.data["results"][0]["url"] == "https://example.test/workspaces"
    assert "Authorized search result" in result.content
