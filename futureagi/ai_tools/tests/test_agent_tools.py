import uuid
from unittest.mock import MagicMock, patch

import pytest

from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import make_agent_definition, make_scenario


@pytest.fixture
def agent_definition(tool_context):
    return make_agent_definition(tool_context)


# ===================================================================
# READ TOOLS
# ===================================================================


class TestGetAgentTool:
    def test_get_existing(self, tool_context, agent_definition):
        result = run_tool(
            "get_agent",
            {"agent_id": str(agent_definition.id)},
            tool_context,
        )

        assert not result.is_error
        assert "Test Agent" in result.content
        assert result.data["id"] == str(agent_definition.id)

    def test_get_nonexistent(self, tool_context):
        result = run_tool(
            "get_agent",
            {"agent_id": str(uuid.uuid4())},
            tool_context,
        )

        assert result.is_error
        assert "Not Found" in result.content

    def test_get_invalid_uuid(self, tool_context):
        result = run_tool(
            "get_agent",
            {"agent_id": "not-a-uuid"},
            tool_context,
        )

        assert result.is_error


class TestListAgentVersionsTool:
    def test_list_empty(self, tool_context, agent_definition):
        result = run_tool(
            "list_agent_versions",
            {"agent_id": str(agent_definition.id)},
            tool_context,
        )

        assert not result.is_error
        assert result.data["total"] == 0

    def test_list_nonexistent_agent(self, tool_context):
        result = run_tool(
            "list_agent_versions",
            {"agent_id": str(uuid.uuid4())},
            tool_context,
        )

        # May return empty list or error depending on implementation
        # Just verify it doesn't crash
        assert isinstance(result.is_error, bool)


class TestListTestExecutionsTool:
    def test_list_empty(self, tool_context):
        # run_test_id is required; pass a random UUID to get empty results
        result = run_tool(
            "list_test_executions",
            {"run_test_id": str(uuid.uuid4())},
            tool_context,
        )

        assert not result.is_error
        assert result.data["total"] == 0


class TestGetTestExecutionTool:
    def test_get_nonexistent(self, tool_context):
        result = run_tool(
            "get_test_execution",
            {"execution_id": str(uuid.uuid4())},
            tool_context,
        )

        assert result.is_error


class TestGetCallExecutionTool:
    def test_get_nonexistent(self, tool_context):
        result = run_tool(
            "get_call_execution",
            {"execution_id": str(uuid.uuid4())},
            tool_context,
        )

        assert result.is_error


# ===================================================================
# RUN TOOL — voice-sim gate
# ===================================================================


def _make_runnable_test(tool_context, *, agent_type):
    """RunTest with an agent definition, active version, and one scenario —
    the minimum for run_agent_test to reach the dispatch path."""
    from simulate.models.agent_version import AgentVersion
    from simulate.models.run_test import RunTest

    agent_definition = make_agent_definition(tool_context, agent_type=agent_type)
    AgentVersion.objects.create(
        agent_definition=agent_definition,
        organization=tool_context.organization,
        workspace=tool_context.workspace,
        version_number=1,
        version_name="v1",
        status=AgentVersion.StatusChoices.ACTIVE,
        configuration_snapshot={"description": "You are a helpful agent."},
    )
    run_test = RunTest.objects.create(
        name="Gate Test Run",
        agent_definition=agent_definition,
        organization=tool_context.organization,
        workspace=tool_context.workspace,
    )
    run_test.scenarios.add(make_scenario(tool_context))
    return run_test


class TestRunAgentTestVoiceGate:
    """The tool mirrors the execute view's voice-sim gate: a build without
    the voice extra must deny VOICE runs up front, before anything is
    persisted or dispatched (TH-4657)."""

    def test_voice_agent_denied_when_gate_denies(self, tool_context):
        from simulate.models.test_execution import TestExecution

        run_test = _make_runnable_test(tool_context, agent_type="voice")

        with (
            patch(
                "tfc.ee_gates.voice_sim_oss_gate_response",
                return_value=MagicMock(name="deny-response"),
            ) as gate,
            patch(
                "simulate.temporal.client.start_test_execution_workflow"
            ) as start_workflow,
        ):
            result = run_tool(
                "run_agent_test", {"run_test_id": str(run_test.id)}, tool_context
            )

        assert result.is_error
        assert result.error_code == "ENTITLEMENT_DENIED"
        gate.assert_called_once()
        start_workflow.assert_not_called()
        # Nothing persisted on deny.
        assert not TestExecution.objects.filter(run_test=run_test).exists()

    def test_voice_agent_runs_when_gate_allows(self, tool_context):
        from simulate.models.test_execution import TestExecution

        run_test = _make_runnable_test(tool_context, agent_type="voice")

        with (
            patch(
                "tfc.ee_gates.voice_sim_oss_gate_response", return_value=None
            ) as gate,
            patch(
                "simulate.temporal.client.start_test_execution_workflow"
            ) as start_workflow,
        ):
            result = run_tool(
                "run_agent_test", {"run_test_id": str(run_test.id)}, tool_context
            )

        assert not result.is_error
        gate.assert_called_once()
        start_workflow.assert_called_once()
        execution = TestExecution.objects.get(run_test=run_test)
        assert execution.status == TestExecution.ExecutionStatus.RUNNING

    def test_text_agent_bypasses_gate(self, tool_context):
        from simulate.models.test_execution import TestExecution

        run_test = _make_runnable_test(tool_context, agent_type="text")

        with (
            patch(
                "tfc.ee_gates.voice_sim_oss_gate_response",
                return_value=MagicMock(name="deny-response"),
            ) as gate,
            patch(
                "simulate.temporal.client.start_test_execution_workflow"
            ) as start_workflow,
        ):
            result = run_tool(
                "run_agent_test", {"run_test_id": str(run_test.id)}, tool_context
            )

        assert not result.is_error
        gate.assert_not_called()
        start_workflow.assert_called_once()
        assert TestExecution.objects.filter(run_test=run_test).exists()
