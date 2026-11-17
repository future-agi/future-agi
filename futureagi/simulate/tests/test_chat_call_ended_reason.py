"""A chat that fails shows a fixed reason for how its call ended.

The reason is stored on the call, returned with the call list and shown on the
call's detail page. When a chat fails to start or stops on an error, the call
shows one fixed sentence, and the error itself goes to the server log.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simulate.models import AgentDefinition, CallExecution, RunTest, Scenarios
from simulate.models.test_execution import TestExecution as SimulationTestExecution
from simulate.serializers.test_execution import CallExecutionSerializer
from simulate.services import chat_sim
from simulate.services.test_executor import TestExecutor
from simulate.tasks import chat_sim as chat_sim_tasks
from simulate.utils.ended_reason import EndedReasonSource, recorded_ended_reason_source
from simulate.views.run_results_v3 import build_call_execution_detail

CHAT_COULD_NOT_START = "The chat could not start because of an error. Re-run it."
CHAT_STOPPED = "The chat stopped because of an error. Re-run it."
USAGE_CHECK_FAILED = "The usage check could not be completed, so the call was ended."
# Made-up error text, worded as a provider or library error might be.
ERROR_TEXT = (
    "request to https://provider.example.test/v1/chat failed for key sk-example-0000"
)
ERROR_PARTS = ("provider.example.test", "https://", "sk-example")
ALLOWED = (True, 0, 0, None)


def _create_text_call(organization, workspace, call_status):
    agent_definition = AgentDefinition.objects.create(
        agent_name=f"Ended Reason Chat Agent {workspace.id}",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="Agent for ended reason tests",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    scenario = Scenarios.objects.create(
        name=f"Ended Reason Scenario {workspace.id}",
        description="Scenario for ended reason tests",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
    )
    run_test = RunTest.objects.create(
        name=f"Ended Reason Run {workspace.id}",
        description="Run for ended reason tests",
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    test_execution = SimulationTestExecution.objects.create(
        run_test=run_test,
        status=SimulationTestExecution.ExecutionStatus.RUNNING,
        total_scenarios=1,
        total_calls=1,
        agent_definition=agent_definition,
    )
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        status=call_status,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
    )


def _start_chat(call, organization, workspace, error):
    with (
        patch.object(TestExecutor, "_check_call_balance", return_value=ALLOWED),
        patch.object(chat_sim, "_get_chat_service", side_effect=error),
        pytest.raises(Exception, match="Error initiating chat"),
    ):
        chat_sim.initiate_chat(call, organization, workspace)


def _start_chat_on_error(call, organization, workspace):
    _start_chat(call, organization, workspace, Exception(ERROR_TEXT))


def _start_chat_on_value_error(call, organization, workspace):
    _start_chat(call, organization, workspace, ValueError(ERROR_TEXT))


def _run_conversation(call, organization, workspace):
    with patch(
        "simulate.services.prompt_based_agent_adapter."
        "create_adapter_from_call_execution",
        side_effect=Exception(ERROR_TEXT),
    ):
        result = chat_sim.run_prompt_based_conversation(call, organization, workspace)
    assert result is False


def _send_message(call, organization, workspace):
    with (
        patch.object(chat_sim, "_get_chat_service", side_effect=Exception(ERROR_TEXT)),
        pytest.raises(Exception, match="Error sending message to chat"),
    ):
        chat_sim.send_message_to_chat(call, organization, workspace, messages=[])


def _run_chat_task(call, organization, workspace):
    with (
        patch(
            "ee.usage.services.metering.check_usage",
            return_value=SimpleNamespace(allowed=True, reason=None),
        ),
        patch.object(chat_sim, "initiate_chat", side_effect=Exception(ERROR_TEXT)),
    ):
        result = chat_sim_tasks.run_single_prompt_chat._original_func(str(call.id))
    assert result is False


@pytest.mark.integration
@pytest.mark.parametrize(
    ("fail", "call_status", "sentence"),
    [
        (
            _start_chat_on_error,
            CallExecution.CallStatus.PENDING,
            CHAT_COULD_NOT_START,
        ),
        (
            _start_chat_on_value_error,
            CallExecution.CallStatus.PENDING,
            CHAT_COULD_NOT_START,
        ),
        (_run_conversation, CallExecution.CallStatus.ONGOING, CHAT_STOPPED),
        (_send_message, CallExecution.CallStatus.ONGOING, CHAT_STOPPED),
        (_run_chat_task, CallExecution.CallStatus.ONGOING, CHAT_STOPPED),
    ],
    ids=["start", "start-value-error", "conversation", "message", "chat-task"],
)
def test_a_failed_chat_shows_only_its_fixed_reason(
    organization, workspace, fail, call_status, sentence
):
    call = _create_text_call(organization, workspace, call_status)

    fail(call, organization, workspace)

    call.refresh_from_db()
    assert call.status == CallExecution.CallStatus.FAILED
    assert call.ended_reason == sentence
    listed = CallExecutionSerializer(call).data
    assert listed["ended_reason"] == sentence
    detail = build_call_execution_detail(call, workspace=workspace)
    assert detail["ended_reason"] == sentence
    for answer in (listed, detail):
        text = json.dumps(answer, default=str)
        for part in ERROR_PARTS:
            assert part not in text


@pytest.mark.requires_ee
@pytest.mark.integration
def test_prompt_chat_whose_usage_check_fails_shows_usage_sentence(
    organization, workspace
):
    call = _create_text_call(organization, workspace, CallExecution.CallStatus.ONGOING)

    with (
        patch(
            "ee.usage.services.metering.check_usage",
            side_effect=Exception(ERROR_TEXT),
        ),
        patch.object(chat_sim, "initiate_chat") as initiate_chat,
    ):
        result = chat_sim_tasks.run_single_prompt_chat._original_func(str(call.id))

    assert result is False
    call.refresh_from_db()
    assert call.status == CallExecution.CallStatus.FAILED
    assert call.ended_reason == USAGE_CHECK_FAILED
    metadata = json.dumps(call.call_metadata, default=str)
    for part in ERROR_PARTS:
        assert part not in call.ended_reason
        assert part not in metadata
    assert recorded_ended_reason_source(call) == EndedReasonSource.FIXED
    initiate_chat.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "reason",
    [
        "Session not found",
        "Chat has already ended",
        "Simulator returned an empty message twice",
    ],
)
def test_a_chat_the_service_refused_keeps_the_services_reason(
    organization, workspace, reason
):
    call = _create_text_call(organization, workspace, CallExecution.CallStatus.ONGOING)
    call.call_metadata = {"chat_session_id": "session-1"}
    call.save(update_fields=["call_metadata"])
    service = SimpleNamespace(
        get_session=lambda session_id: SimpleNamespace(success=True, messages=[]),
        send_message=lambda **kwargs: SimpleNamespace(success=False, error=reason),
    )

    with (
        patch.object(chat_sim, "_get_chat_service", return_value=service),
        pytest.raises(Exception, match="Error sending message to chat"),
    ):
        chat_sim.send_message_to_chat(call, organization, workspace, messages=[])

    call.refresh_from_db()
    assert call.status == CallExecution.CallStatus.FAILED
    assert call.ended_reason == reason
