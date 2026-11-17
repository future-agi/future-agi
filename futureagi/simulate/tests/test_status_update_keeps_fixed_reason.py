"""What a chat call shows after the SDK reports it failed.

The SDK (agent-simulate) reports a chat that could not start, or a turn whose
message could not be sent, with a status update to "failed" whose reason is
the HTTP status and the app's answer. A call that already shows one of our
fixed sentences, with the source recorded for that sentence, keeps the
sentence and its record. Any other call shows "Error processing simulation",
as before. The text the SDK sent is never stored, answered or logged. A call
in another organization or workspace answers "Call execution not found", as
before.
"""

import uuid
from types import SimpleNamespace

import pytest
from structlog.testing import capture_logs

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from simulate.models import AgentDefinition, CallExecution, RunTest, Scenarios
from simulate.models.test_execution import TestExecution as SimulationTestExecution
from simulate.serializers.test_execution import CallExecutionSerializer
from simulate.services.chat_sim import CHAT_COULD_NOT_START, CHAT_STOPPED
from simulate.services.test_executor import (
    CALL_COULD_NOT_START,
    CALL_STOPPED,
    USAGE_CHECK_FAILED,
)
from simulate.tests.test_ended_reason_source import (
    _marker,
    _rerun_one_call,
    _shared_answer,
)
from simulate.utils.ended_reason import (
    ENDED_REASON_SOURCE_KEY,
    EndedReasonSource,
    recorded_ended_reason_source,
    set_ended_reason,
)

# Made-up text in the shape the SDK sends: the HTTP status, then the answer.
SDK_TEXT = "500: made-up text 7f3a"
# What a call shows when the SDK reports it failed and it shows no fixed
# sentence of ours with its source.
ERROR_PROCESSING = "Error processing simulation"
# Text a reason may hold that is not one of our fixed sentences.
FREE_TEXT = "The runner stopped after three turns"
# A refusal's own reason, as the usage check stores it for a chat it refused.
USAGE_REFUSAL = "Usage limit exceeded"
NOT_FOUND = "Call execution not found"
# A metadata key some other code wrote, kept as stored.
OTHER_METADATA = {"example_key": "example-value"}


def _chat_run(organization, workspace):
    """A chat simulation run in ``workspace`` with one scenario."""
    agent_definition = AgentDefinition.objects.create(
        agent_name=f"Status Update Agent {workspace.id}",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="Agent for status update tests",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    scenario = Scenarios.objects.create(
        name=f"Status Update Scenario {workspace.id}",
        description="Scenario for status update tests",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
    )
    run = RunTest.objects.create(
        name=f"Status Update Run {workspace.id}",
        description="Run for status update tests",
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
    )
    run.scenarios.add(scenario)
    execution = SimulationTestExecution.objects.create(
        run_test=run,
        status=SimulationTestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        agent_definition=agent_definition,
    )
    return SimpleNamespace(execution=execution, scenario=scenario)


@pytest.fixture
def chat_run(organization, workspace):
    return _chat_run(organization, workspace)


def _chat_call(run, *, ended_reason=None, source=None, **fields):
    """A chat call, ongoing unless ``fields`` give another status. With
    ``source``, its reason is written the way our code writes one; without,
    the call carries no source."""
    fields.setdefault("status", CallExecution.CallStatus.ONGOING)
    call = CallExecution.objects.create(
        test_execution=run.execution,
        scenario=run.scenario,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        ended_reason=ended_reason,
        call_metadata=dict(OTHER_METADATA),
        **fields,
    )
    if source is not None:
        call.save(update_fields=set_ended_reason(call, ended_reason, source))
    return call


def _report_failed(client, call_id):
    """The status update the SDK sends when a chat could not start or a turn
    could not be sent."""
    return client.patch(
        f"/simulate/call-executions/{call_id}/",
        {"status": "failed", "ended_reason": SDK_TEXT},
        format="json",
    )


def _stored_row(call):
    """Every column of ``call``'s row as stored."""
    return CallExecution.no_workspace_objects.filter(id=call.id).values().get()


def _reset_for_rerun(call, run):
    """The reset a rerun makes to each call it runs again."""
    call.reset_to_default()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("shown", "source", "start_status"),
    [
        pytest.param(
            CHAT_COULD_NOT_START,
            EndedReasonSource.FIXED,
            CallExecution.CallStatus.FAILED,
            id="chat-could-not-start",
        ),
        pytest.param(
            CHAT_STOPPED,
            EndedReasonSource.FIXED,
            CallExecution.CallStatus.FAILED,
            id="chat-stopped",
        ),
        pytest.param(
            USAGE_CHECK_FAILED,
            EndedReasonSource.USAGE,
            CallExecution.CallStatus.FAILED,
            id="usage-check-failed",
        ),
        pytest.param(
            CHAT_STOPPED,
            EndedReasonSource.FIXED,
            CallExecution.CallStatus.ONGOING,
            id="chat-stopped-while-ongoing",
        ),
        pytest.param(
            CALL_COULD_NOT_START,
            EndedReasonSource.FIXED,
            CallExecution.CallStatus.FAILED,
            id="call-could-not-start",
        ),
        pytest.param(
            CALL_STOPPED,
            EndedReasonSource.FIXED,
            CallExecution.CallStatus.FAILED,
            id="call-stopped",
        ),
        pytest.param(
            USAGE_REFUSAL,
            EndedReasonSource.USAGE,
            CallExecution.CallStatus.FAILED,
            id="usage-refusal",
        ),
    ],
)
def test_a_failed_chat_keeps_the_sentence_it_shows_when_the_sdk_reports_it(
    chat_run, auth_client, user, workspace, shown, source, start_status
):
    # ended_at starts unset on purpose, so the test shows the route still
    # saves the end time (and, from ONGOING, the failed status) when it keeps
    # the reason.
    call = _chat_call(
        chat_run,
        ended_reason=shown,
        source=source,
        status=start_status,
    )
    metadata = dict(call.call_metadata)

    response = _report_failed(auth_client, call.id)

    assert response.status_code == 200, response.content
    answer = response.json()
    assert set(answer) == set(CallExecutionSerializer.Meta.fields)
    assert answer["status"] == CallExecution.CallStatus.FAILED
    assert answer["ended_reason"] == shown
    assert SDK_TEXT not in response.content.decode()
    assert ENDED_REASON_SOURCE_KEY not in response.content.decode()
    call.refresh_from_db()
    assert call.status == CallExecution.CallStatus.FAILED
    assert call.ended_at is not None
    assert call.ended_reason == shown
    # The record is the one the chat code wrote, and every other key stays.
    assert call.call_metadata == metadata
    assert recorded_ended_reason_source(call) == source
    assert SDK_TEXT not in str(_stored_row(call))
    assert _shared_answer(call, user, workspace)["ended_reason"] == shown


@pytest.mark.integration
@pytest.mark.parametrize(
    ("before", "source"),
    [
        pytest.param(None, None, id="no-reason"),
        pytest.param(FREE_TEXT, None, id="free-text-without-source"),
        pytest.param(CHAT_COULD_NOT_START, None, id="fixed-sentence-without-source"),
        pytest.param(
            FREE_TEXT, EndedReasonSource.ORGANIZATION, id="free-text-with-source"
        ),
        # Ordinary end reasons and a cancel are not reasons a call failed.
        pytest.param(
            "Chat ended by simulator",
            EndedReasonSource.FIXED,
            id="ended-by-simulator",
        ),
        pytest.param(
            "Max conversation turns reached",
            EndedReasonSource.FIXED,
            id="max-turns",
        ),
        pytest.param(
            "Cancelled by user", EndedReasonSource.FIXED, id="cancelled-by-user"
        ),
    ],
)
def test_a_call_without_a_recorded_fixed_sentence_shows_error_processing_simulation(
    chat_run, auth_client, user, workspace, before, source
):
    call = _chat_call(chat_run, ended_reason=before, source=source)

    response = _report_failed(auth_client, call.id)

    assert response.status_code == 200, response.content
    assert response.json()["status"] == CallExecution.CallStatus.FAILED
    assert response.json()["ended_reason"] == ERROR_PROCESSING
    assert SDK_TEXT not in response.content.decode()
    call.refresh_from_db()
    assert call.status == CallExecution.CallStatus.FAILED
    assert call.ended_at is not None
    assert call.ended_reason == ERROR_PROCESSING
    assert call.call_metadata == {
        **OTHER_METADATA,
        ENDED_REASON_SOURCE_KEY: _marker(EndedReasonSource.FIXED, ERROR_PROCESSING),
    }
    assert SDK_TEXT not in str(_stored_row(call))
    shared = _shared_answer(call, user, workspace)
    assert shared["ended_reason"] == ERROR_PROCESSING


@pytest.mark.integration
@pytest.mark.parametrize(
    "rerun",
    [_rerun_one_call, _reset_for_rerun],
    ids=["chosen-calls", "reset"],
)
def test_a_rerun_call_reported_failed_shows_error_processing_simulation(
    chat_run, auth_client, user, workspace, rerun
):
    # The earlier run could not start the chat.
    call = _chat_call(
        chat_run,
        ended_reason=CHAT_COULD_NOT_START,
        source=EndedReasonSource.FIXED,
        status=CallExecution.CallStatus.FAILED,
    )

    rerun(call, chat_run)

    call.refresh_from_db()
    assert call.ended_reason is None
    assert ENDED_REASON_SOURCE_KEY not in call.call_metadata

    response = _report_failed(auth_client, call.id)

    assert response.status_code == 200, response.content
    assert response.json()["ended_reason"] == ERROR_PROCESSING
    call.refresh_from_db()
    assert call.status == CallExecution.CallStatus.FAILED
    assert call.ended_reason == ERROR_PROCESSING
    assert call.call_metadata[ENDED_REASON_SOURCE_KEY] == _marker(
        EndedReasonSource.FIXED, ERROR_PROCESSING
    )
    assert _shared_answer(call, user, workspace)["ended_reason"] == ERROR_PROCESSING


@pytest.mark.integration
def test_an_agent_error_after_a_turn_shows_error_processing_simulation_completed(
    chat_run, auth_client, user, workspace
):
    # The customer's agent raised after a turn finished: the SDK reports the
    # chat completed, so its evaluations run, with its own fixed text.
    call = _chat_call(chat_run)

    response = auth_client.patch(
        f"/simulate/call-executions/{call.id}/",
        {"status": "completed", "ended_reason": ERROR_PROCESSING},
        format="json",
    )

    assert response.status_code == 200, response.content
    assert response.json()["status"] == CallExecution.CallStatus.COMPLETED
    assert response.json()["ended_reason"] == ERROR_PROCESSING
    call.refresh_from_db()
    assert call.status == CallExecution.CallStatus.COMPLETED
    assert call.ended_at is not None
    assert call.ended_reason == ERROR_PROCESSING
    # Stored as the client's text, which can carry a provider's error.
    assert call.call_metadata[ENDED_REASON_SOURCE_KEY] == _marker(
        EndedReasonSource.PROVIDER_ERROR, ERROR_PROCESSING
    )
    assert recorded_ended_reason_source(call) == EndedReasonSource.PROVIDER_ERROR
    # A shared link shows it as stored: it is one of our fixed sentences.
    assert _shared_answer(call, user, workspace)["ended_reason"] == ERROR_PROCESSING


@pytest.mark.integration
@pytest.mark.parametrize("owner", ["other-organization", "other-workspace"])
def test_a_failed_report_for_a_call_outside_the_workspace_answers_not_found(
    auth_client, organization, user, owner
):
    if owner == "other-organization":
        organization = Organization.objects.create(name="Status Update Other Org")
    workspace = Workspace.objects.create(
        name=f"Status Update {owner}",
        organization=organization,
        is_default=owner == "other-organization",
        is_active=True,
        created_by=user,
    )
    call = _chat_call(
        _chat_run(organization, workspace),
        ended_reason=CHAT_COULD_NOT_START,
        source=EndedReasonSource.FIXED,
        status=CallExecution.CallStatus.FAILED,
    )
    before = _stored_row(call)

    response = _report_failed(auth_client, call.id)
    missing = _report_failed(auth_client, uuid.uuid4())

    assert response.status_code == 404, response.content
    # The same answer as for a call that does not exist.
    assert missing.status_code == 404, missing.content
    assert response.json() == missing.json()
    assert response.json()["message"] == NOT_FOUND
    assert response.json()["code"] == "not_found"
    assert _stored_row(call) == before


@pytest.mark.integration
@pytest.mark.parametrize(
    ("before", "source"),
    [
        pytest.param(CHAT_COULD_NOT_START, EndedReasonSource.FIXED, id="kept"),
        pytest.param(None, None, id="replaced"),
    ],
)
def test_a_failed_report_logs_none_of_the_text_the_sdk_sent(
    chat_run, auth_client, before, source
):
    call = _chat_call(
        chat_run,
        ended_reason=before,
        source=source,
        status=CallExecution.CallStatus.FAILED,
    )

    with capture_logs() as logs:
        response = _report_failed(auth_client, call.id)

    assert response.status_code == 200, response.content
    # The route logs the update once, so the logs read here are its own.
    updates = [
        entry for entry in logs if entry.get("event") == "call_execution_status_updated"
    ]
    assert len(updates) == 1
    assert SDK_TEXT not in str(logs)
