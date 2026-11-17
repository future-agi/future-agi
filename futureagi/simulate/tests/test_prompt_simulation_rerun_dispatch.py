"""Re-running a finished prompt-based simulation runs its calls again.

A person presses Re-run on one call or on a whole execution of a finished
prompt-based simulation. The calls go back to the prompt chat dispatcher as
queued calls, the way a first run leaves them, and each runs the version the
simulation has selected as a new conversation. A re-queued call has the same
30 minutes before the chat timeout as a new call, counted from the re-run.
A re-run is refused before anything is reset when the selected version was
deleted, and a call whose conversation is still running is left alone. Agent
simulations keep re-running through their call workflow, and a simulation of
another organization cannot be re-run.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models import (
    AgentDefinition,
    CallExecution,
    CallExecutionSnapshot,
    RunTest,
    Scenarios,
)
from simulate.models.chat_message import ChatMessageModel
from simulate.models.run_test import CreateCallExecution
from simulate.models.test_execution import TestExecution as SimulationExecution
from simulate.services.call_rerun import clear_call_for_rerun
from simulate.services.chat_sim import run_prompt_based_conversation
from simulate.tasks import chat_sim as prompt_chat_tasks

CALL_WORKFLOW = "simulate.temporal.client.rerun_call_executions"
VOICE_GATE = "simulate.views.run_test._voice_sim_gate_response"
HOSTED_ELIGIBLE = "simulate.views.run_test._hosted_execution_eligible"
MODEL = "simulate.services.prompt_based_agent_adapter.RunPrompt"
SIMULATOR = "simulate.services.chat_sim.send_message_to_chat"
ONE_CALL = "one_call"
WHOLE_EXECUTION = "whole_execution"
QUEUED = CallExecution.CallStatus.REGISTERED
IN_CONVERSATION = CallExecution.CallStatus.ONGOING
FINISHED = CallExecution.CallStatus.COMPLETED
TIMED_OUT = CallExecution.CallStatus.FAILED
RUNNING = SimulationExecution.ExecutionStatus.RUNNING
VERSION_DELETED = (
    "Prompt version has been deleted. Please update the simulation with a valid "
    "version."
)
STILL_RUNNING = "This call is still running. Re-run it after it finishes."
LONG_AGO = timedelta(hours=2)
PAST_CHAT_TIMEOUT = timedelta(minutes=31)
FIRST_RUN_SESSION = "first-run-session"
FIRST_RUN_TEXT = "FIRST RUN CONVERSATION"
MARKERS = (("v1", "VERSION_ONE_MARKER"), ("v2", "VERSION_TWO_MARKER"))


def _prompt_simulation(organization, workspace):
    """A finished prompt-based simulation, stored as its first run left it."""
    template = PromptTemplate.objects.create(
        name=f"Re-run prompt {uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
    )
    versions = [
        PromptVersion.objects.create(
            original_template=template,
            template_version=label,
            prompt_config_snapshot=[
                {
                    "messages": [
                        {"role": "system", "content": marker + " {{customer}}"}
                    ],
                    "configuration": {"model": "gpt-4o-mini"},
                }
            ],
        )
        for label, marker in MARKERS
    ]
    run_test = RunTest.objects.create(
        name="Prompt simulation",
        source_type=RunTest.SourceTypes.PROMPT,
        prompt_template=template,
        prompt_version=versions[0],
        organization=organization,
        workspace=workspace,
    )
    scenario = Scenarios.objects.create(
        name="Prompt simulation scenario",
        source="Prompt simulation rows",
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    execution = SimulationExecution.objects.create(
        run_test=run_test,
        status=SimulationExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
    )
    call = CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        status=FINISHED,
        ended_reason="Conversation ended",
        eval_outputs={"eval": {"score": 0.5}},
        call_metadata={
            "source_type": "prompt",
            "prompt_template_id": str(template.id),
            "prompt_version_id": str(versions[0].id),
            "row_data": {"customer": "Ada"},
            "chat_session_id": FIRST_RUN_SESSION,
            "eval_started": True,
            "eval_completed": True,
        },
    )
    ChatMessageModel.objects.create(
        role=ChatMessageModel.RoleChoices.USER,
        call_execution=call,
        messages=[FIRST_RUN_TEXT],
        content=[{"role": "user", "content": FIRST_RUN_TEXT}],
        session_id=FIRST_RUN_SESSION,
        organization=organization,
        workspace=workspace,
        tokens=7,
    )
    return SimpleNamespace(
        run_test=run_test, versions=versions, execution=execution, call=call
    )


def _agent_simulation(organization, workspace):
    """A finished simulation of a voice agent."""
    agent = AgentDefinition.objects.create(
        agent_name="Re-run voice agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        description="Voice agent whose simulation is re-run",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    run_test = RunTest.objects.create(
        name="Voice agent simulation",
        agent_definition=agent,
        organization=organization,
        workspace=workspace,
    )
    scenario = Scenarios.objects.create(
        name="Voice agent scenario",
        source="Voice agent rows",
        organization=organization,
        workspace=workspace,
        agent_definition=agent,
    )
    run_test.scenarios.add(scenario)
    execution = SimulationExecution.objects.create(
        run_test=run_test,
        status=SimulationExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        agent_definition=agent,
    )
    call = CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        phone_number="+1234567890",
        simulation_call_type=CallExecution.SimulationCallType.VOICE,
        status=FINISHED,
        service_provider_call_id="voice-call",
        call_metadata={
            "base_prompt": "You are a caller",
            "voice_settings": {"provider": "elevenlabs"},
            "call_direction": "inbound",
            "eval_started": True,
            "eval_completed": True,
        },
    )
    return SimpleNamespace(run_test=run_test, execution=execution, call=call)


def _rerun(client, simulation, scope):
    """Press Re-run on the finished simulation, as its results page does."""
    if scope == ONE_CALL:
        return client.post(
            f"/simulate/test-executions/{simulation.execution.id}/rerun-calls/",
            {
                "rerun_type": "call_and_eval",
                "call_execution_ids": [str(simulation.call.id)],
            },
            format="json",
        )
    return client.post(
        f"/simulate/run-tests/{simulation.run_test.id}/rerun-test-executions/",
        {
            "rerun_type": "call_and_eval",
            "test_execution_ids": [str(simulation.execution.id)],
        },
        format="json",
    )


def _rerun_outcome(response, scope):
    """The successful and failed re-runs the response reports for the execution."""
    data = response.json()
    if scope == WHOLE_EXECUTION:
        (data,) = data["results"]
    return data["successful_reruns"], data["failed_reruns"]


def _prompt_dispatcher_tick():
    """One scheduled tick of the prompt chat dispatcher; returns the task start."""
    task = prompt_chat_tasks.run_single_prompt_chat
    with patch.object(task, "apply_async") as start:
        prompt_chat_tasks.process_prompt_based_chat_simulations._original_func()
    return start


def _chat_monitor_tick():
    """One scheduled tick of the chat run monitor; returns the task start."""
    task = prompt_chat_tasks.monitor_test_execution_for_chat
    with patch.object(task, "apply_async") as start:
        prompt_chat_tasks.monitor_chat_test_executions._original_func()
    return start


def _chat_timeout_sweep():
    """One scheduled tick of the sweep that fails chats left unfinished."""
    prompt_chat_tasks.monitor_chat_timeout_call_executions._original_func()


def _store_as_written_ago(call, age):
    """Store the call's row as last written ``age`` ago."""
    then = timezone.now() - age
    CallExecution.objects.filter(pk=call.pk).update(created_at=then, updated_at=then)


def _stale_queued_call(organization, workspace):
    """A prompt-based call queued two hours ago that never ran."""
    call = _prompt_simulation(organization, workspace).call
    CallExecution.objects.filter(pk=call.pk).update(status=QUEUED, ended_reason=None)
    _store_as_written_ago(call, LONG_AGO)
    return call


def _run_claimed_call(call):
    """Run the conversation of a call the dispatcher claimed, as its task does."""
    call = CallExecution.objects.select_related(
        "test_execution__run_test__organization",
        "test_execution__run_test__workspace",
    ).get(pk=call.pk)
    run_test = call.test_execution.run_test

    def echo_model_input(**kwargs):
        model = Mock()
        model.litellm_response.return_value = (kwargs["messages"][0]["content"], {})
        return model

    with (
        patch(MODEL, side_effect=echo_model_input) as model,
        patch(SIMULATOR, return_value={"chat_ended": True}) as send,
    ):
        completed = run_prompt_based_conversation(
            call, run_test.organization, run_test.workspace, max_turns=1
        )
    return SimpleNamespace(completed=completed, model=model, send=send)


def _assert_handed_to_prompt_chat_dispatcher(simulation, response, scope):
    assert response.status_code == 200, response.content
    successful, failed = _rerun_outcome(response, scope)
    assert failed == []
    assert successful == [str(simulation.call.id)]

    call = simulation.call
    call.refresh_from_db()
    assert call.status == QUEUED
    assert call.eval_outputs == {}
    assert "eval_started" not in call.call_metadata
    assert CallExecutionSnapshot.objects.filter(call_execution=call).exists()
    assert not CreateCallExecution.objects.filter(call_execution=call).exists()

    execution = simulation.execution
    execution.refresh_from_db()
    assert execution.status == RUNNING
    assert execution.picked_up_by_executor is False
    monitored = _chat_monitor_tick()
    monitored.assert_called_once_with(args=(str(execution.id),))

    started = _prompt_dispatcher_tick()
    started.assert_called_once_with(args=(str(call.id),))
    call.refresh_from_db()
    assert call.status == CallExecution.CallStatus.ONGOING


def test_rerun_of_one_call_hands_it_to_the_prompt_chat_dispatcher(
    auth_client, organization, workspace
):
    simulation = _prompt_simulation(organization, workspace)

    with patch(CALL_WORKFLOW) as call_workflow:
        response = _rerun(auth_client, simulation, ONE_CALL)

    _assert_handed_to_prompt_chat_dispatcher(simulation, response, ONE_CALL)
    call_workflow.assert_not_called()


def test_rerun_of_a_whole_execution_hands_its_calls_to_the_prompt_chat_dispatcher(
    auth_client, organization, workspace
):
    simulation = _prompt_simulation(organization, workspace)

    with patch(CALL_WORKFLOW) as call_workflow:
        response = _rerun(auth_client, simulation, WHOLE_EXECUTION)

    _assert_handed_to_prompt_chat_dispatcher(simulation, response, WHOLE_EXECUTION)
    call_workflow.assert_not_called()


@pytest.mark.parametrize("scope", [ONE_CALL, WHOLE_EXECUTION])
def test_requeued_call_runs_the_selected_version_as_a_new_conversation(
    auth_client, organization, workspace, scope
):
    simulation = _prompt_simulation(organization, workspace)
    selected = simulation.versions[1]
    RunTest.no_workspace_objects.filter(pk=simulation.run_test.pk).update(
        prompt_version=selected
    )

    with patch(CALL_WORKFLOW):
        response = _rerun(auth_client, simulation, scope)
    assert response.status_code == 200, response.content
    started = _prompt_dispatcher_tick()
    started.assert_called_once_with(args=(str(simulation.call.id),))

    result = _run_claimed_call(simulation.call)

    assert result.completed is True
    # The selected version's prompt, and nothing of the first run's conversation.
    model_input = result.model.call_args.kwargs["messages"]
    contents = [message["content"] for message in model_input]
    assert contents == ["VERSION_TWO_MARKER Ada"]
    sent = result.send.call_args.kwargs["messages"][0].content
    assert sent == "VERSION_TWO_MARKER Ada"
    call = simulation.call
    call.refresh_from_db()
    assert call.call_metadata["prompt_version_id"] == str(selected.id)
    assert not ChatMessageModel.no_workspace_objects.filter(
        call_execution=call
    ).exists()


@pytest.mark.parametrize("scope", [ONE_CALL, WHOLE_EXECUTION])
def test_requeued_call_gets_the_full_chat_timeout_from_the_rerun(
    auth_client, organization, workspace, scope
):
    simulation = _prompt_simulation(organization, workspace)
    _store_as_written_ago(simulation.call, LONG_AGO)
    never_rerun = _stale_queued_call(organization, workspace)

    with patch(CALL_WORKFLOW):
        response = _rerun(auth_client, simulation, scope)
    assert response.status_code == 200, response.content

    _chat_timeout_sweep()
    call = simulation.call
    call.refresh_from_db()
    assert call.status == QUEUED
    assert call.completed_at is None
    # The same tick still times out a call that has waited 30 minutes.
    never_rerun.refresh_from_db()
    assert never_rerun.status == TIMED_OUT

    started = _prompt_dispatcher_tick()
    started.assert_called_once_with(args=(str(call.id),))
    _chat_timeout_sweep()
    call.refresh_from_db()
    assert call.status == IN_CONVERSATION
    assert call.completed_at is None


@pytest.mark.parametrize("queued", ["first_run", "rerun_over_30_minutes_ago"])
def test_prompt_call_queued_over_30_minutes_ago_still_times_out(
    auth_client, organization, workspace, queued
):
    if queued == "first_run":
        call = _stale_queued_call(organization, workspace)
    else:
        simulation = _prompt_simulation(organization, workspace)
        call = simulation.call
        _store_as_written_ago(call, LONG_AGO)
        with patch(CALL_WORKFLOW):
            response = _rerun(auth_client, simulation, ONE_CALL)
        assert response.status_code == 200, response.content
        rerun_at = timezone.now() - PAST_CHAT_TIMEOUT
        CallExecutionSnapshot.objects.filter(call_execution=call).update(
            created_at=rerun_at, snapshot_timestamp=rerun_at
        )
        CallExecution.objects.filter(pk=call.pk).update(updated_at=rerun_at)

    _chat_timeout_sweep()

    call.refresh_from_db()
    assert call.status == TIMED_OUT
    assert call.completed_at is not None


def test_agent_chat_call_reset_for_a_rerun_still_times_out_by_its_row(
    organization, workspace
):
    simulation = _agent_simulation(organization, workspace)
    CallExecution.objects.filter(pk=simulation.call.pk).update(
        simulation_call_type=CallExecution.SimulationCallType.TEXT
    )
    call = CallExecution.objects.get(pk=simulation.call.pk)
    clear_call_for_rerun(call)
    _store_as_written_ago(call, LONG_AGO)

    _chat_timeout_sweep()

    call.refresh_from_db()
    assert call.status == TIMED_OUT


@pytest.mark.parametrize(
    "deleted, scope",
    [
        ("version", ONE_CALL),
        ("version", WHOLE_EXECUTION),
        ("template", ONE_CALL),
        ("template", WHOLE_EXECUTION),
    ],
)
def test_rerun_is_refused_when_the_selected_version_was_deleted(
    auth_client, organization, workspace, deleted, scope
):
    simulation = _prompt_simulation(organization, workspace)
    selected = simulation.versions[0]
    if deleted == "version":
        PromptVersion.objects.filter(pk=selected.pk).update(deleted=True)
    else:
        PromptTemplate.objects.filter(pk=selected.original_template_id).update(
            deleted=True
        )

    with patch(CALL_WORKFLOW) as call_workflow:
        response = _rerun(auth_client, simulation, scope)

    assert response.status_code == 400, response.content
    assert response.json()["message"] == VERSION_DELETED
    call_workflow.assert_not_called()
    call = simulation.call
    call.refresh_from_db()
    assert call.status == FINISHED
    assert call.eval_outputs == {"eval": {"score": 0.5}}
    assert call.call_metadata["prompt_version_id"] == str(selected.id)
    assert not CallExecutionSnapshot.objects.filter(call_execution=call).exists()
    assert ChatMessageModel.no_workspace_objects.filter(call_execution=call).exists()
    _prompt_dispatcher_tick().assert_not_called()


def test_rerun_of_one_call_still_running_leaves_its_conversation_alone(
    auth_client, organization, workspace
):
    simulation = _prompt_simulation(organization, workspace)
    SimulationExecution.objects.filter(pk=simulation.execution.pk).update(
        status=RUNNING
    )
    CallExecution.objects.filter(pk=simulation.call.pk).update(
        status=IN_CONVERSATION, ended_reason=None
    )
    execution = simulation.execution
    execution.refresh_from_db()
    picked_up = execution.picked_up_by_executor

    with patch(CALL_WORKFLOW) as call_workflow:
        response = _rerun(auth_client, simulation, ONE_CALL)

    assert response.status_code == 200, response.content
    successful, failed = _rerun_outcome(response, ONE_CALL)
    assert successful == []
    assert failed == [
        {"call_execution_id": str(simulation.call.id), "error": STILL_RUNNING}
    ]
    call_workflow.assert_not_called()
    call = simulation.call
    call.refresh_from_db()
    assert call.status == IN_CONVERSATION
    assert call.eval_outputs == {"eval": {"score": 0.5}}
    assert call.call_metadata["chat_session_id"] == FIRST_RUN_SESSION
    assert not CallExecutionSnapshot.objects.filter(call_execution=call).exists()
    assert ChatMessageModel.no_workspace_objects.filter(call_execution=call).exists()
    execution.refresh_from_db()
    assert execution.status == RUNNING
    assert execution.picked_up_by_executor is picked_up


def test_rerun_of_one_call_still_queued_in_a_running_run_queues_it_again(
    auth_client, organization, workspace
):
    simulation = _prompt_simulation(organization, workspace)
    SimulationExecution.objects.filter(pk=simulation.execution.pk).update(
        status=RUNNING
    )
    CallExecution.objects.filter(pk=simulation.call.pk).update(
        status=QUEUED, ended_reason=None
    )

    with patch(CALL_WORKFLOW) as call_workflow:
        response = _rerun(auth_client, simulation, ONE_CALL)

    assert response.status_code == 200, response.content
    successful, failed = _rerun_outcome(response, ONE_CALL)
    assert failed == []
    assert successful == [str(simulation.call.id)]
    call_workflow.assert_not_called()
    call = simulation.call
    call.refresh_from_db()
    assert call.status == QUEUED
    assert CallExecutionSnapshot.objects.filter(call_execution=call).exists()


@pytest.mark.parametrize("scope", [ONE_CALL, WHOLE_EXECUTION])
def test_rerun_of_an_agent_simulation_still_starts_its_call_workflow(
    auth_client, organization, workspace, scope
):
    simulation = _agent_simulation(organization, workspace)
    workflow = {"workflow_id": "call-workflow", "merged": False}

    with (
        patch(VOICE_GATE, return_value=None),
        patch(HOSTED_ELIGIBLE, return_value=False),
        patch(CALL_WORKFLOW, return_value=workflow) as call_workflow,
    ):
        response = _rerun(auth_client, simulation, scope)

    assert response.status_code == 200, response.content
    successful, failed = _rerun_outcome(response, scope)
    assert failed == []
    assert successful == [str(simulation.call.id)]
    call_workflow.assert_called_once()
    started = call_workflow.call_args.kwargs
    assert started["test_execution_id"] == str(simulation.execution.id)
    assert started["call_execution_ids"] == [str(simulation.call.id)]
    assert started["eval_only"] is False

    call = simulation.call
    call.refresh_from_db()
    assert call.status == CallExecution.CallStatus.PENDING
    assert CreateCallExecution.objects.filter(call_execution=call).count() == 1
    execution = simulation.execution
    execution.refresh_from_db()
    assert execution.status == RUNNING
    assert execution.picked_up_by_executor is True
    metadata = execution.execution_metadata
    assert metadata["active_rerun_workflow_id"] == "call-workflow"
    _prompt_dispatcher_tick().assert_not_called()


@pytest.mark.parametrize("scope", [ONE_CALL, WHOLE_EXECUTION])
def test_simulation_of_another_organization_cannot_be_rerun(auth_client, user, scope):
    other_organization = Organization.objects.create(name="Other organization")
    other_workspace = Workspace.objects.create(
        name="Other organization workspace",
        organization=other_organization,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    simulation = _prompt_simulation(other_organization, other_workspace)
    recorded = simulation.versions[0]

    with patch(CALL_WORKFLOW) as call_workflow:
        response = _rerun(auth_client, simulation, scope)

    assert response.status_code == 404, response.content
    call_workflow.assert_not_called()
    call = simulation.call
    call.refresh_from_db()
    assert call.status == FINISHED
    assert call.call_metadata["prompt_version_id"] == str(recorded.id)
    assert not CallExecutionSnapshot.objects.filter(call_execution=call).exists()
    assert ChatMessageModel.no_workspace_objects.filter(call_execution=call).exists()
    _prompt_dispatcher_tick().assert_not_called()
