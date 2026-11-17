"""Chat simulation views answer a fixed sentence on an unexpected failure.

The exception text can carry hostnames, paths or ids, so it belongs in the
server log. These tests force a failure inside each endpoint and check that
the status stays the same, the body holds the endpoint's own sentence and no
part of the exception text reaches the client.
"""

import json
import uuid
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, field_validator
from rest_framework import status

from simulate.models import AgentDefinition, CallExecution, RunTest, Scenarios
from simulate.models.test_execution import TestExecution as SimulationTestExecution
from simulate.serializers.test_execution import CallExecutionSerializer
from simulate.views import chat_simulation as chat_simulation_views

INTERNAL_DETAIL = "internal detail /srv/x host=db-internal"
LEAKED_FRAGMENTS = ("internal detail", "/srv/x", "db-internal")
SEND_MESSAGE_URL = "/simulate/call-executions/{call_execution_id}/chat/send-message/"
SDK_CODE_URL = "/simulate/run-tests/{run_test_id}/sdk-code/"
VALIDATION_SENTENCE = "Invalid request data format. Expected SendChatRequest schema."
USAGE_REFUSED_SENTENCE = (
    "The chat could not start because this organization's usage check did not allow it."
)
USAGE_CHECK_FAILED_REASON = (
    "The usage check could not be completed, so the call was ended."
)
# A reason the usage service writes for the organization itself.
UNPAID_INVOICES_REASON = (
    "Your subscription has unpaid invoices. "
    "Update your payment method to restore access."
)
CHAT_TURN = {
    "initiate_chat": False,
    "messages": [{"role": "user", "content": "hello"}],
}


def _raise_internal_error(*args, **kwargs):
    raise Exception(INTERNAL_DETAIL)


class _ProviderReply(BaseModel):
    host: str

    @field_validator("host")
    @classmethod
    def reject_host(cls, value):
        raise ValueError(INTERNAL_DETAIL)


def _raise_internal_validation_error(*args, **kwargs):
    _ProviderReply.model_validate({"host": "reply"})


def _assert_no_internal_detail(response):
    body = response.content.decode()
    for fragment in LEAKED_FRAGMENTS:
        assert fragment not in body


def _create_text_call_execution(organization, workspace, execution_status):
    agent_definition = AgentDefinition.objects.create(
        agent_name=f"Error Body Chat Agent {workspace.id}",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="Agent for chat error body tests",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    scenario = Scenarios.objects.create(
        name=f"Error Body Scenario {workspace.id}",
        description="Scenario for chat error body tests",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
    )
    run_test = RunTest.objects.create(
        name=f"Error Body Run {workspace.id}",
        description="Run for chat error body tests",
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    test_execution = SimulationTestExecution.objects.create(
        run_test=run_test,
        status=execution_status,
        total_scenarios=1,
        total_calls=1,
        agent_definition=agent_definition,
    )
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        status=CallExecution.CallStatus.PENDING,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
    )


@pytest.fixture
def running_chat_call(db, organization, workspace):
    return _create_text_call_execution(
        organization, workspace, SimulationTestExecution.ExecutionStatus.RUNNING
    )


@pytest.fixture
def pending_chat_call(db, organization, workspace):
    return _create_text_call_execution(
        organization, workspace, SimulationTestExecution.ExecutionStatus.PENDING
    )


@pytest.mark.integration
@pytest.mark.api
class TestChatSendMessageErrorBody:
    def test_send_failure_answers_fixed_sentence(
        self, auth_client, monkeypatch, running_chat_call
    ):
        monkeypatch.setattr(
            chat_simulation_views, "send_message_to_chat", _raise_internal_error
        )

        response = auth_client.post(
            SEND_MESSAGE_URL.format(call_execution_id=running_chat_call.id),
            CHAT_TURN,
            format="json",
        )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert response.data["message"] == "Failed to send message."
        _assert_no_internal_detail(response)

    def test_initiate_failure_answers_fixed_sentence(
        self, auth_client, monkeypatch, pending_chat_call
    ):
        monkeypatch.setattr(
            chat_simulation_views, "initiate_chat", _raise_internal_error
        )

        response = auth_client.post(
            SEND_MESSAGE_URL.format(call_execution_id=pending_chat_call.id),
            {"initiate_chat": True},
            format="json",
        )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert response.data["message"] == "Failed to send message."
        _assert_no_internal_detail(response)

    @pytest.mark.parametrize(
        "check_reason",
        [
            # The limits refused: a reason the usage service wrote.
            "Usage limit exceeded",
            # The check itself failed and closed: its text carries an
            # exception's text, which must not reach the body.
            f"Error checking balance: {INTERNAL_DETAIL}",
        ],
        ids=["limit", "check-failed"],
    )
    def test_usage_refusal_says_the_usage_check_stopped_the_chat(
        self, auth_client, monkeypatch, pending_chat_call, check_reason
    ):
        monkeypatch.setattr(
            "simulate.services.test_executor.TestExecutor._check_call_balance",
            lambda self, **kwargs: (False, 0, 0, check_reason),
        )

        response = auth_client.post(
            SEND_MESSAGE_URL.format(call_execution_id=pending_chat_call.id),
            {"initiate_chat": True},
            format="json",
        )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert response.data["message"] == USAGE_REFUSED_SENTENCE
        _assert_no_internal_detail(response)
        pending_chat_call.refresh_from_db()
        assert pending_chat_call.status == CallExecution.CallStatus.FAILED

    def test_internal_validation_error_answers_fixed_sentence(
        self, auth_client, monkeypatch, running_chat_call
    ):
        monkeypatch.setattr(
            chat_simulation_views,
            "send_message_to_chat",
            _raise_internal_validation_error,
        )

        response = auth_client.post(
            SEND_MESSAGE_URL.format(call_execution_id=running_chat_call.id),
            CHAT_TURN,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["message"] == VALIDATION_SENTENCE
        _assert_no_internal_detail(response)


def _start_chat(auth_client, call_execution):
    return auth_client.post(
        SEND_MESSAGE_URL.format(call_execution_id=call_execution.id),
        {"initiate_chat": True},
        format="json",
    )


@pytest.mark.integration
@pytest.mark.api
class TestUsageRefusalEndedReason:
    """The reason a refused chat's call shows, on the call and in the call
    execution list."""

    def test_a_failed_usage_check_shows_the_fixed_reason(
        self, auth_client, monkeypatch, pending_chat_call
    ):
        monkeypatch.setattr(
            "simulate.services.test_executor.check_usage", _raise_internal_error
        )

        response = _start_chat(auth_client, pending_chat_call)

        assert response.data["message"] == USAGE_REFUSED_SENTENCE
        pending_chat_call.refresh_from_db()
        assert pending_chat_call.status == CallExecution.CallStatus.FAILED
        assert pending_chat_call.ended_reason == USAGE_CHECK_FAILED_REASON
        served = CallExecutionSerializer(pending_chat_call).data
        assert served["ended_reason"] == USAGE_CHECK_FAILED_REASON
        served_text = json.dumps(served, default=str)
        for fragment in LEAKED_FRAGMENTS:
            assert fragment not in served_text

    def test_a_refusal_keeps_the_usage_services_own_reason(
        self, auth_client, monkeypatch, pending_chat_call
    ):
        monkeypatch.setattr(
            "simulate.services.test_executor.check_usage",
            lambda *args, **kwargs: SimpleNamespace(
                allowed=False, reason=UNPAID_INVOICES_REASON
            ),
        )

        response = _start_chat(auth_client, pending_chat_call)

        assert response.data["message"] == USAGE_REFUSED_SENTENCE
        pending_chat_call.refresh_from_db()
        assert pending_chat_call.ended_reason == UNPAID_INVOICES_REASON
        served = CallExecutionSerializer(pending_chat_call).data
        assert served["ended_reason"] == UNPAID_INVOICES_REASON


@pytest.mark.integration
@pytest.mark.api
class TestChatSDKCodeErrorBody:
    def test_sdk_code_failure_answers_fixed_sentence(self, auth_client, monkeypatch):
        monkeypatch.setattr(
            chat_simulation_views, "run_test_workspace_filter", _raise_internal_error
        )

        response = auth_client.get(SDK_CODE_URL.format(run_test_id=uuid.uuid4()))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["message"] == "Failed to generate SDK code."
        _assert_no_internal_detail(response)
