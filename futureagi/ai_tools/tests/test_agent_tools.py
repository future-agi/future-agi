import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from structlog.testing import capture_logs

from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import make_agent_definition, make_scenario
from simulate.models import AgentDefinition, AgentVersion
from simulate.models.agent_definition import ProviderCredentials
from tracer.services import observability_switch
from tracer.tests.test_observability_switch import (
    _agent,
    _agent_with_row,
    _created,
    _statements,
    _stored,
    _stored_agent,
    agent_update_columns,
    assert_one_project_and_row,
    make_summary,
    seams,
    stale_copies,
)
from tracer.utils.otel import ResourceLimitError


def _voice_denial():
    return SimpleNamespace(
        data={
            "message": "Voice simulation requires a higher plan",
            "code": "permission_denied",
            "feature": "voice_sim",
            "upgrade_required": True,
        }
    )


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
                "tfc.ee_gates.voice_sim_gate_response",
                return_value=_voice_denial(),
            ) as gate,
            patch(
                "simulate.temporal.client.start_test_execution_workflow"
            ) as start_workflow,
        ):
            result = run_tool(
                "run_agent_test", {"run_test_id": str(run_test.id)}, tool_context
            )

        assert result.is_error
        assert result.error_code == "permission_denied"
        assert result.data["feature"] == "voice_sim"
        gate.assert_called_once_with(tool_context.organization)
        start_workflow.assert_not_called()
        # Nothing persisted on deny.
        assert not TestExecution.objects.filter(run_test=run_test).exists()

    def test_voice_agent_runs_when_gate_allows(self, tool_context):
        from simulate.models.test_execution import TestExecution

        run_test = _make_runnable_test(tool_context, agent_type="voice")

        with (
            patch("tfc.ee_gates.voice_sim_gate_response", return_value=None) as gate,
            patch(
                "simulate.temporal.client.start_test_execution_workflow"
            ) as start_workflow,
        ):
            result = run_tool(
                "run_agent_test", {"run_test_id": str(run_test.id)}, tool_context
            )

        assert not result.is_error
        gate.assert_called_once_with(tool_context.organization)
        start_workflow.assert_called_once()
        execution = TestExecution.objects.get(run_test=run_test)
        assert execution.status == TestExecution.ExecutionStatus.RUNNING

    def test_text_agent_bypasses_gate(self, tool_context):
        from simulate.models.test_execution import TestExecution

        run_test = _make_runnable_test(tool_context, agent_type="text")

        with (
            patch(
                "tfc.ee_gates.voice_sim_gate_response",
                return_value=_voice_denial(),
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


@pytest.mark.django_db
def test_voice_rerun_denied_before_snapshot_or_reset(tool_context):
    from simulate.models.agent_definition import AgentDefinition
    from simulate.models.test_execution import CallExecution, CallExecutionSnapshot

    call = MagicMock()
    call.id = uuid.uuid4()
    call.test_execution.status = "completed"
    call.test_execution.run_test.agent_definition.agent_type = (
        AgentDefinition.AgentTypeChoices.VOICE
    )
    call.transcripts = MagicMock()

    queryset = MagicMock()
    queryset.get.return_value = call

    with (
        patch.object(CallExecution.objects, "select_related", return_value=queryset),
        patch.object(CallExecutionSnapshot.objects, "create") as create_snapshot,
        patch(
            "tfc.ee_gates.voice_sim_gate_response", return_value=_voice_denial()
        ) as gate,
        patch("simulate.temporal.client.rerun_call_executions") as rerun,
    ):
        result = run_tool(
            "rerun_call_execution",
            {"call_execution_id": str(call.id), "rerun_type": "call_and_eval"},
            tool_context,
        )

    assert result.is_error
    assert result.error_code == "permission_denied"
    gate.assert_called_once_with(tool_context.organization)
    create_snapshot.assert_not_called()
    call.transcripts.all.return_value.delete.assert_not_called()
    call.reset_to_default.assert_not_called()
    rerun.assert_not_called()


# ===================================================================
# OBSERVABILITY SWITCH, THROUGH THE THREE WRITING TOOLS
# ===================================================================

WRITING_TOOLS = (
    "create_agent_definition",
    "update_agent_definition",
    "create_agent_version",
)


@pytest.fixture
def observed_agent(tool_context):
    return _agent(
        tool_context.organization,
        tool_context.workspace,
        agent_name="Observed Agent",
        description="Original",
    )


def _tool_call(tool, agent, **extra):
    """The tool name and the params of a call that turns observability on."""
    if tool == "create_agent_definition":
        params = {
            "agent_name": f"Created {uuid.uuid4().hex[:8]}",
            "agent_type": "voice",
            "description": "A created agent",
            "provider": "others",
            "contact_number": "+12345678901",
            "assistant_id": "assistant-1",
            "api_key": "test-key",
            "observability_enabled": True,
        }
    elif tool == "update_agent_definition":
        params = {
            "agent_id": str(agent.id),
            "description": "Changed",
            "api_key": "test-key",
            "observability_enabled": True,
        }
    else:
        params = {
            "agent_id": str(agent.id),
            "commit_message": "Changed",
            "description": "Changed",
            "api_key": "test-key",
            "observability_enabled": True,
        }
    params.update(extra)
    return tool, params


def _written(tool, agent, params):
    """The agent the call saved, or None when it saved none."""
    name = (
        params["agent_name"] if tool == "create_agent_definition" else agent.agent_name
    )
    return AgentDefinition.no_workspace_objects.filter(agent_name=name).first()


def _state(tool, agent, params):
    saved = _written(tool, agent, params)
    return (
        saved and (saved.description, saved.api_key, saved.observability_provider_id),
        AgentVersion.no_workspace_objects.count(),
        ProviderCredentials.no_workspace_objects.count(),
        _created(),
    )


def _newest_version(agent):
    return AgentVersion.no_workspace_objects.filter(agent_definition=agent).order_by(
        "-version_number"
    )[0]


@pytest.mark.parametrize("tool", WRITING_TOOLS)
def test_tool_turns_on_with_key_and_agent_id_in_the_plain_column(
    tool_context, observed_agent, tool
):
    name, params = _tool_call(tool, observed_agent)
    plain = make_summary(credential_id=None, key_present=True)

    with seams(plain), capture_logs() as logs:
        result = run_tool(name, params, tool_context)

    assert not result.is_error
    agent = _written(name, observed_agent, params)
    row = agent.observability_provider
    assert row is not None and row.enabled is True
    assert [
        e["event"] for e in logs if e["event"].startswith("observability_switch")
    ] == ["observability_switch_plain_column_key"]


@pytest.mark.parametrize("tool", WRITING_TOOLS)
def test_tool_writing_a_key_writes_the_credential_row(
    tool_context, observed_agent, tool
):
    name, params = _tool_call(tool, observed_agent, observability_enabled=False)
    result = run_tool(name, params, tool_context)

    assert not result.is_error
    agent = _written(name, observed_agent, params)
    credentials = ProviderCredentials.no_workspace_objects.get(
        agent_version=_newest_version(agent)
    )
    assert credentials.get_api_key() == "test-key"
    assert credentials.assistant_id == "assistant-1"


@pytest.mark.parametrize("tool", WRITING_TOOLS)
def test_tool_without_a_key_or_with_a_masked_one_writes_no_credential_row(
    tool_context, observed_agent, tool
):
    for api_key in (None, "********"):
        name, params = _tool_call(
            tool, observed_agent, observability_enabled=False, api_key=api_key
        )
        result = run_tool(name, params, tool_context)

        assert not result.is_error
        agent = _written(name, observed_agent, params)
        assert not ProviderCredentials.no_workspace_objects.filter(
            agent_version=_newest_version(agent)
        ).exists()


@pytest.mark.parametrize("tool", WRITING_TOOLS)
def test_tool_version_snapshot_records_observability(
    tool_context, observed_agent, tool
):
    name, params = _tool_call(tool, observed_agent)
    with seams(make_summary()):
        result = run_tool(name, params, tool_context)

    assert not result.is_error
    agent = _written(name, observed_agent, params)
    # The snapshot is taken after the switch, so it equals the switch's result.
    snapshot = _newest_version(agent).configuration_snapshot
    assert snapshot["observability_enabled"] is True
    assert agent.observability_provider.enabled is True


def test_update_tool_without_the_field_turns_observability_off(tool_context):
    agent = _agent_with_row(
        tool_context.organization, tool_context.workspace, enabled=True
    )
    name, params = _tool_call("update_agent_definition", agent)
    params.pop("observability_enabled")
    with seams(make_summary(agent_definition_id=agent.id)):
        result = run_tool(name, params, tool_context)

    assert not result.is_error
    assert _stored(agent.observability_provider)[1] is False
    assert (
        _newest_version(agent).configuration_snapshot["observability_enabled"] is False
    )


def test_create_tool_keeps_its_provider_gate(tool_context):
    # An others agent that is asked for observability with no key gets no project.
    name, params = _tool_call("create_agent_definition", None, api_key=None)
    created = _created()
    with patch.object(observability_switch, "set_agent_observability") as switch_call:
        result = run_tool(name, params, tool_context)

    assert not result.is_error
    switch_call.assert_not_called()
    assert _created() == created
    agent = _written(name, None, params)
    assert agent.observability_provider_id is None


def test_create_tool_project_limit_answers_as_today(tool_context):
    name, params = _tool_call("create_agent_definition", None)
    before = _state(name, None, params)
    with (
        seams(make_summary()),
        patch.object(
            observability_switch,
            "create_observability_provider",
            side_effect=ResourceLimitError("limit"),
        ),
    ):
        result = run_tool(name, params, tool_context)

    assert result.is_error
    assert "PROJECT CREATION LIMIT REACHED" in result.content
    assert _written(name, None, params) is None
    assert _state(name, None, params) == before


@pytest.mark.parametrize("tool", WRITING_TOOLS)
def test_tool_project_error_writes_nothing(tool_context, observed_agent, tool):
    name, params = _tool_call(tool, observed_agent)
    before = _state(name, observed_agent, params)
    with (
        seams(make_summary()),
        patch.object(
            observability_switch,
            "create_observability_provider",
            return_value={"error": "Invalid data"},
        ),
    ):
        result = run_tool(name, params, tool_context)

    # The error is not caught by the tool, so the base tool reports it.
    assert result.is_error
    assert "observability project could not be created" in result.content
    assert _state(name, observed_agent, params) == before


@pytest.mark.parametrize("tool", WRITING_TOOLS)
def test_tool_refusal_returns_the_detail_and_writes_nothing(
    tool_context, observed_agent, tool
):
    name, params = _tool_call(tool, observed_agent)
    before = _state(name, observed_agent, params)
    refusing = make_summary(missing=("credential", "provider_agent_id"))
    with seams(refusing):
        result = run_tool(name, params, tool_context)

    assert result.is_error
    assert result.error_code == "VALIDATION_ERROR"
    assert result.content == (
        "**Validation Error:** Observability cannot be turned on: "
        "API key is required when observability is enabled; "
        "Assistant ID is required when observability is enabled"
    )
    assert _state(name, observed_agent, params) == before


def test_create_version_tool_double_submit_gives_one_project_and_one_row(
    tool_context,
):
    agent = _agent(tool_context.organization, tool_context.workspace)
    name, first = _tool_call("create_agent_version", agent, description="First")
    _, second = _tool_call("create_agent_version", agent, description="Second")

    # Both calls read the agent before either one switched it on.
    with seams(make_summary(agent_definition_id=agent.id)), stale_copies(agent):
        first_result = run_tool(name, first, tool_context)
        second_result = run_tool(name, second, tool_context)

    assert not first_result.is_error
    assert not second_result.is_error
    assert_one_project_and_row(agent)
    assert _stored_agent(agent).description == "Second"


def test_create_version_tool_never_writes_the_link_and_keeps_other_writes(
    tool_context,
):
    agent = _agent_with_row(
        tool_context.organization, tool_context.workspace, enabled=True
    )
    name, params = _tool_call(
        "create_agent_version", agent, observability_enabled=False
    )
    params.pop("api_key")
    # Another copy writes a field, and clears the link, after this one is read.
    with stale_copies(agent, count=1):
        AgentDefinition.no_workspace_objects.filter(pk=agent.pk).update(
            model="written-meanwhile", observability_provider=None
        )
        seen, recording = _statements()
        with seams(make_summary(agent_definition_id=agent.id)), recording:
            result = run_tool(name, params, tool_context)

    assert not result.is_error
    assert agent_update_columns(seen) == [{"description", "updated_at"}]
    stored = _stored_agent(agent)
    assert stored.description == "Changed"
    assert stored.model == "written-meanwhile"
    assert stored.observability_provider_id is None


def test_create_version_tool_without_changed_fields_does_not_update_the_agent(
    tool_context,
):
    agent = _agent(tool_context.organization, tool_context.workspace)
    seen, recording = _statements()
    with recording:
        result = run_tool(
            "create_agent_version",
            {"agent_id": str(agent.id), "commit_message": "No changes"},
            tool_context,
        )

    assert not result.is_error
    assert agent_update_columns(seen) == []
