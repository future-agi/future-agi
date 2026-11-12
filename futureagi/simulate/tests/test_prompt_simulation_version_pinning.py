"""Queued prompt-based calls run the prompt version recorded when they were queued."""

from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models import CallExecution, RunTest, Scenarios
from simulate.models.test_execution import TestExecution as SimulationExecution
from simulate.services.chat_sim import run_prompt_based_conversation


@pytest.fixture
def prompt_run(db, organization, workspace):
    template = PromptTemplate.objects.create(
        name="Pinned prompt", organization=organization, workspace=workspace
    )
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
        for version, marker in [("v1", "VERSION_A_MARKER"), ("v2", "VERSION_B_MARKER")]
    ]
    run = RunTest.objects.create(
        name="Version pinning simulation",
        source_type="prompt",
        prompt_template=template,
        prompt_version=versions[0],
        organization=organization,
        workspace=workspace,
    )
    scenario = Scenarios.objects.create(
        name="Version pinning scenario",
        source="Version pinning regression",
        organization=organization,
        workspace=workspace,
    )
    return SimpleNamespace(
        run=run, template=template, versions=versions, scenario=scenario
    )


def _queue_call(prompt_run, version):
    execution = SimulationExecution.objects.create(run_test=prompt_run.run)
    return CallExecution.objects.create(
        test_execution=execution,
        scenario=prompt_run.scenario,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        status=CallExecution.CallStatus.REGISTERED,
        call_metadata={
            "source_type": "prompt",
            "prompt_template_id": str(prompt_run.template.id),
            "prompt_version_id": str(version.id),
            "row_data": {"customer": "Ada"},
        },
    )


def _dispatch(call, organization, workspace):
    # A dispatcher loads fresh relations, not the RunTest cached at queue time.
    call = CallExecution.objects.select_related("test_execution__run_test").get(
        pk=call.pk
    )
    call.status = CallExecution.CallStatus.ONGOING
    call.save(update_fields=["status"])

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
            call, organization, workspace, max_turns=1
        )
    return SimpleNamespace(completed=completed, call=call, model=model, send=send)


def test_queued_call_keeps_version_after_next_version_selection(
    prompt_run, organization, workspace, auth_client
):
    old_call = _queue_call(prompt_run, prompt_run.versions[0])
    response = auth_client.patch(
        f"/simulate/prompt-templates/{prompt_run.template.id}/simulations/{prompt_run.run.id}/",
        {"prompt_version_id": str(prompt_run.versions[1].id)},
        format="json",
    )
    assert response.status_code == 200
    prompt_run.run.refresh_from_db()
    assert prompt_run.run.prompt_version_id == prompt_run.versions[1].id

    # Dispatch the original call before starting any second execution.
    original = _dispatch(old_call, organization, workspace)
    assert original.completed is True
    assert (
        original.send.call_args.kwargs["messages"][0].content
        == "VERSION_A_MARKER Ada"
    )
    assert original.model.call_args.kwargs["organization_id"] == str(organization.id)
    assert original.model.call_args.kwargs["workspace_id"] == str(workspace.id)
    old_call.refresh_from_db()
    assert old_call.call_metadata["prompt_version_id"] == str(prompt_run.versions[0].id)

    # The changed selector is still honored by future executions.
    new_call = _queue_call(prompt_run, prompt_run.run.prompt_version)
    following = _dispatch(new_call, organization, workspace)
    assert following.completed is True
    assert (
        following.send.call_args.kwargs["messages"][0].content
        == "VERSION_B_MARKER Ada"
    )
    prompt_run.run.refresh_from_db()
    assert prompt_run.run.prompt_version_id == prompt_run.versions[1].id


@pytest.mark.parametrize(
    "unavailable", ["missing", "null", "unknown", "deleted", "other_template"]
)
def test_unavailable_recorded_version_never_falls_back_to_current_selection(
    prompt_run, organization, workspace, unavailable
):
    call = _queue_call(prompt_run, prompt_run.versions[0])
    prompt_run.run.prompt_version = prompt_run.versions[1]
    prompt_run.run.save(update_fields=["prompt_version"])
    if unavailable == "missing":
        call.call_metadata.pop("prompt_version_id")
    elif unavailable == "null":
        call.call_metadata["prompt_version_id"] = None
    elif unavailable == "unknown":
        call.call_metadata["prompt_version_id"] = str(uuid4())
    elif unavailable == "deleted":
        PromptVersion.objects.filter(pk=prompt_run.versions[0].pk).update(deleted=True)
    else:
        other_template = PromptTemplate.objects.create(
            name="Another prompt", organization=organization, workspace=workspace
        )
        other_version = PromptVersion.objects.create(
            original_template=other_template,
            template_version="v1",
            prompt_config_snapshot=prompt_run.versions[0].prompt_config_snapshot,
        )
        call.call_metadata["prompt_version_id"] = str(other_version.id)
    call.save(update_fields=["call_metadata"])

    result = _dispatch(call, organization, workspace)

    assert result.completed is False
    result.model.assert_not_called()
    result.send.assert_not_called()
    result.call.refresh_from_db()
    assert result.call.status == CallExecution.CallStatus.FAILED


@pytest.mark.parametrize("scope", ["organization", "template"])
def test_recorded_version_is_resolved_only_in_execution_scope(
    prompt_run, organization, workspace, scope
):
    call = _queue_call(prompt_run, prompt_run.versions[0])
    if scope == "template":
        other_template = PromptTemplate.objects.create(
            name="Another prompt", organization=organization, workspace=workspace
        )
        other_version = PromptVersion.objects.create(
            original_template=other_template,
            template_version="v1",
            prompt_config_snapshot=prompt_run.versions[0].prompt_config_snapshot,
        )
        call.call_metadata["prompt_version_id"] = str(other_version.id)
        call.save(update_fields=["call_metadata"])
    result = _dispatch(
        call,
        SimpleNamespace(id=uuid4()) if scope == "organization" else organization,
        workspace,
    )

    assert result.completed is False
    result.model.assert_not_called()
    result.send.assert_not_called()
    result.call.refresh_from_db()
    assert result.call.status == CallExecution.CallStatus.FAILED


def test_recorded_version_resolves_in_whichever_workspace_the_call_runs(
    prompt_run, organization
):
    call = _queue_call(prompt_run, prompt_run.versions[0])
    other_workspace = SimpleNamespace(id=uuid4())

    result = _dispatch(call, organization, other_workspace)

    assert result.completed is True
    assert (
        result.send.call_args.kwargs["messages"][0].content == "VERSION_A_MARKER Ada"
    )
    # Provider keys are still looked up in the workspace the call runs in.
    assert result.model.call_args.kwargs["workspace_id"] == str(other_workspace.id)
