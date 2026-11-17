"""Prompt-based run tests on rows as they are stored, run and re-run.

A queued call runs the prompt version recorded on it, found by the
simulation's organization and prompt template, in whichever of the
organization's workspaces the run test and the template sit. A re-run a
person starts runs the version the simulation has selected at that moment.

Running a call changes neither the template nor its versions. A re-run writes
only the calls it re-runs, and a re-run from a workspace that does not hold the
simulation is refused and changes no call.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models import CallExecution, CallExecutionSnapshot, RunTest, Scenarios
from simulate.models.test_execution import TestExecution as SimulationExecution
from simulate.services.chat_sim import run_prompt_based_conversation

NO_VERSION = "Select a prompt version, then re-run this simulation."
RERUN_DISPATCH = "simulate.temporal.client.rerun_call_executions"
MARKERS = {"v1": "VERSION_ONE_MARKER", "v2": "VERSION_TWO_MARKER"}
QUEUED = CallExecution.CallStatus.REGISTERED
FINISHED = CallExecution.CallStatus.COMPLETED

# Where the run test and its template sit. An organization holds one default
# workspace at a time, so a second workspace of the same organization stands in
# for another default one.
PLACEMENTS = {
    "same_workspace": ("default", "default"),
    "run_test_without_workspace": (None, "default"),
    "template_without_workspace": ("default", None),
    "template_in_second_workspace": ("default", "second"),
}
ACROSS_WORKSPACES = [name for name in PLACEMENTS if name != "same_workspace"]


@pytest.fixture
def workspaces(organization, workspace, user):
    second = Workspace.objects.create(
        name="Second workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    return {"default": workspace, "second": second, None: None}


def _set_workspace(model, row, workspace):
    # Stored as given: an insert would put the current workspace on an empty one.
    model.no_workspace_objects.filter(pk=row.pk).update(workspace=workspace)
    row.refresh_from_db()


def _prompt_template(organization, workspace):
    template = PromptTemplate.objects.create(
        name=f"Stored prompt {uuid4().hex[:8]}", organization=organization
    )
    _set_workspace(PromptTemplate, template, workspace)
    versions = [
        PromptVersion.objects.create(
            original_template=template,
            template_version=version,
            prompt_config_snapshot=[
                {
                    "messages": [
                        {"role": "system", "content": marker + " {{customer}}"}
                    ],
                    "configuration": {"model": "gpt-4o-mini"},
                }
            ],
        )
        for version, marker in MARKERS.items()
    ]
    return template, versions


def _simulation(organization, run_workspace, template, versions):
    run_test = RunTest.objects.create(
        name="Stored prompt simulation",
        source_type=RunTest.SourceTypes.PROMPT,
        prompt_template=template,
        prompt_version=versions[0],
        organization=organization,
    )
    _set_workspace(RunTest, run_test, run_workspace)
    scenario = Scenarios.objects.create(
        name="Stored prompt scenario",
        source="Stored prompt rows",
        organization=organization,
    )
    return SimpleNamespace(
        run_test=run_test, template=template, versions=versions, scenario=scenario
    )


def _placed_simulation(organization, workspaces, placement):
    run_workspace, template_workspace = PLACEMENTS[placement]
    template, versions = _prompt_template(organization, workspaces[template_workspace])
    return _simulation(organization, workspaces[run_workspace], template, versions)


def _finished_execution(simulation):
    return SimulationExecution.objects.create(
        run_test=simulation.run_test,
        status=SimulationExecution.ExecutionStatus.COMPLETED,
    )


def _queue_call(simulation, version, status, execution=None):
    """A call row as the executor writes it for a prompt-based run test."""
    return CallExecution.objects.create(
        test_execution=execution or _finished_execution(simulation),
        scenario=simulation.scenario,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        status=status,
        call_metadata={
            "source_type": "prompt",
            "prompt_template_id": str(simulation.template.id),
            "prompt_version_id": str(version.id),
            "row_data": {"customer": "Ada"},
        },
    )


def _select_version(simulation, version):
    RunTest.no_workspace_objects.filter(pk=simulation.run_test.pk).update(
        prompt_version=version
    )


def _dispatch(call):
    """Run a claimed call with the run test's own organization and workspace."""
    call = CallExecution.objects.select_related(
        "test_execution__run_test__organization",
        "test_execution__run_test__workspace",
    ).get(pk=call.pk)
    call.status = CallExecution.CallStatus.ONGOING
    call.save(update_fields=["status"])
    run_test = call.test_execution.run_test

    def echo_model_input(**kwargs):
        model = Mock()
        model.litellm_response.return_value = (kwargs["messages"][0]["content"], {})
        return model

    with (
        patch(
            "simulate.services.prompt_based_agent_adapter.RunPrompt",
            side_effect=echo_model_input,
        ) as model,
        patch(
            "simulate.services.chat_sim.send_message_to_chat",
            return_value={"chat_ended": True},
        ) as send,
    ):
        completed = run_prompt_based_conversation(
            call, run_test.organization, run_test.workspace, max_turns=1
        )
    call.refresh_from_db()
    return SimpleNamespace(completed=completed, call=call, model=model, send=send)


def _sent_text(result):
    return result.send.call_args.kwargs["messages"][0].content


def _stored_prompt(simulation):
    """The simulation's template and its versions, as stored."""
    template = PromptTemplate._base_manager.filter(pk=simulation.template.pk)
    versions = PromptVersion._base_manager.filter(
        original_template_id=simulation.template.pk
    ).order_by("template_version")
    return list(template.values()), list(versions.values())


def _rerun_one_call(client, call):
    with patch(RERUN_DISPATCH, return_value={"workflow_id": "rerun", "merged": False}):
        return client.post(
            f"/simulate/test-executions/{call.test_execution_id}/rerun-calls/",
            {"rerun_type": "call_and_eval", "call_execution_ids": [str(call.id)]},
            format="json",
        )


def _rerun_execution(client, simulation, execution):
    with patch(RERUN_DISPATCH, return_value={"workflow_id": "rerun", "merged": False}):
        return client.post(
            f"/simulate/run-tests/{simulation.run_test.id}/rerun-test-executions/",
            {"rerun_type": "call_and_eval", "test_execution_ids": [str(execution.id)]},
            format="json",
        )


@pytest.mark.parametrize("placement", ACROSS_WORKSPACES)
def test_queued_call_runs_its_version_wherever_the_organization_keeps_the_template(
    organization, workspaces, placement
):
    simulation = _placed_simulation(organization, workspaces, placement)
    call = _queue_call(simulation, simulation.versions[0], QUEUED)

    result = _dispatch(call)

    assert result.completed is True
    assert _sent_text(result) == "VERSION_ONE_MARKER Ada"
    assert result.call.status != CallExecution.CallStatus.FAILED
    # Provider keys are still looked up in the run test's own workspace.
    run_workspace = simulation.run_test.workspace_id
    expected_workspace = str(run_workspace) if run_workspace else None
    assert result.model.call_args.kwargs["workspace_id"] == expected_workspace


def test_queued_call_is_refused_for_a_template_of_another_organization(
    organization, workspace
):
    other_organization = Organization.objects.create(name="Other organization")
    template, versions = _prompt_template(other_organization, None)
    simulation = _simulation(organization, workspace, template, versions)
    call = _queue_call(simulation, versions[0], QUEUED)

    result = _dispatch(call)

    assert result.completed is False
    result.model.assert_not_called()
    result.send.assert_not_called()
    assert result.call.status == CallExecution.CallStatus.FAILED


def test_queued_call_is_refused_for_a_version_of_another_template(
    organization, workspace, workspaces
):
    simulation = _placed_simulation(organization, workspaces, "same_workspace")
    _, other_versions = _prompt_template(organization, workspace)
    call = _queue_call(simulation, other_versions[0], QUEUED)

    result = _dispatch(call)

    assert result.completed is False
    result.model.assert_not_called()
    result.send.assert_not_called()
    assert result.call.status == CallExecution.CallStatus.FAILED


@pytest.mark.parametrize("placement", ["same_workspace", "run_test_without_workspace"])
def test_rerun_of_one_call_runs_the_version_the_simulation_has_selected(
    organization, workspaces, auth_client, placement
):
    simulation = _placed_simulation(organization, workspaces, placement)
    first, second = simulation.versions
    call = _queue_call(simulation, first, FINISHED)
    _select_version(simulation, second)

    response = _rerun_one_call(auth_client, call)

    assert response.status_code == 200, response.content
    call.refresh_from_db()
    assert call.call_metadata["prompt_version_id"] == str(second.id)
    assert call.call_metadata["row_data"] == {"customer": "Ada"}
    result = _dispatch(call)
    assert result.completed is True
    assert _sent_text(result) == "VERSION_TWO_MARKER Ada"
    assert result.call.call_metadata["prompt_version_id"] == str(second.id)


def test_rerun_of_a_whole_execution_runs_the_selected_version_for_every_call(
    organization, workspaces, auth_client
):
    simulation = _placed_simulation(organization, workspaces, "same_workspace")
    first, second = simulation.versions
    execution = _finished_execution(simulation)
    calls = [_queue_call(simulation, first, FINISHED, execution) for _ in range(2)]
    _select_version(simulation, second)

    response = _rerun_execution(auth_client, simulation, execution)

    assert response.status_code == 200, response.content
    for call in calls:
        call.refresh_from_db()
        assert call.call_metadata["prompt_version_id"] == str(second.id)
        assert call.call_metadata["row_data"] == {"customer": "Ada"}
        result = _dispatch(call)
        assert result.completed is True
        assert _sent_text(result) == "VERSION_TWO_MARKER Ada"


def test_rerun_after_the_recorded_version_was_deleted_runs_the_selected_version(
    organization, workspaces, auth_client
):
    simulation = _placed_simulation(organization, workspaces, "same_workspace")
    first, second = simulation.versions
    call = _queue_call(simulation, first, FINISHED)
    PromptVersion.objects.filter(pk=first.pk).update(deleted=True)
    _select_version(simulation, second)

    response = _rerun_one_call(auth_client, call)

    assert response.status_code == 200, response.content
    result = _dispatch(call)
    assert result.completed is True
    assert result.call.status != CallExecution.CallStatus.FAILED
    assert _sent_text(result) == "VERSION_TWO_MARKER Ada"


@pytest.mark.parametrize("scope", ["one_call", "whole_execution"])
def test_rerun_is_refused_when_the_simulation_has_no_selected_version(
    organization, workspaces, auth_client, scope
):
    simulation = _placed_simulation(organization, workspaces, "same_workspace")
    call = _queue_call(simulation, simulation.versions[0], FINISHED)
    _select_version(simulation, None)

    if scope == "one_call":
        response = _rerun_one_call(auth_client, call)
    else:
        response = _rerun_execution(auth_client, simulation, call.test_execution)

    assert response.status_code == 400, response.content
    assert response.json()["message"] == NO_VERSION
    call.refresh_from_db()
    assert call.status == FINISHED
    assert call.call_metadata["prompt_version_id"] == str(simulation.versions[0].id)
    assert not CallExecutionSnapshot.objects.filter(call_execution=call).exists()


@pytest.mark.parametrize("placement", ACROSS_WORKSPACES)
def test_running_a_queued_call_changes_neither_the_template_nor_its_versions(
    organization, workspaces, placement
):
    simulation = _placed_simulation(organization, workspaces, placement)
    call = _queue_call(simulation, simulation.versions[0], QUEUED)
    stored = _stored_prompt(simulation)

    result = _dispatch(call)

    assert result.completed is True
    assert _stored_prompt(simulation) == stored


@pytest.mark.parametrize("scope", ["one_call", "whole_execution"])
def test_rerun_records_the_selected_version_only_on_the_calls_it_reruns(
    organization, workspaces, auth_client, scope
):
    simulation = _placed_simulation(organization, workspaces, "same_workspace")
    first, second = simulation.versions
    execution = _finished_execution(simulation)
    call = _queue_call(simulation, first, FINISHED, execution)
    sibling = _queue_call(simulation, first, FINISHED, execution)
    elsewhere = _queue_call(simulation, first, FINISHED)
    _select_version(simulation, second)
    stored = _stored_prompt(simulation)

    if scope == "one_call":
        response = _rerun_one_call(auth_client, call)
        rerun, kept = [call], [sibling, elsewhere]
    else:
        response = _rerun_execution(auth_client, simulation, execution)
        rerun, kept = [call, sibling], [elsewhere]

    assert response.status_code == 200, response.content
    for row in rerun:
        row.refresh_from_db()
        assert row.call_metadata["prompt_version_id"] == str(second.id)
    for row in kept:
        row.refresh_from_db()
        assert row.status == FINISHED
        assert row.call_metadata["prompt_version_id"] == str(first.id)
        assert not CallExecutionSnapshot.objects.filter(call_execution=row).exists()
    assert _stored_prompt(simulation) == stored


@pytest.mark.parametrize("scope", ["one_call", "whole_execution"])
@pytest.mark.parametrize("placement", ["same_workspace", "run_test_without_workspace"])
def test_rerun_from_another_workspace_is_refused_and_changes_no_call(
    organization, workspaces, auth_client, placement, scope
):
    simulation = _placed_simulation(organization, workspaces, placement)
    first, second = simulation.versions
    call = _queue_call(simulation, first, FINISHED)
    _select_version(simulation, second)
    auth_client.set_workspace(workspaces["second"])

    if scope == "one_call":
        response = _rerun_one_call(auth_client, call)
    else:
        response = _rerun_execution(auth_client, simulation, call.test_execution)

    assert response.status_code == 404, response.content
    call.refresh_from_db()
    assert call.status == FINISHED
    assert call.call_metadata["prompt_version_id"] == str(first.id)
    assert not CallExecutionSnapshot.objects.filter(call_execution=call).exists()
