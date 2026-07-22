"""Contract-debt tests for generated agent prompt optimiser routes."""

from types import SimpleNamespace

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.choices import OwnerChoices, StatusType
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import (
    AgentOptimiser,
    AgentOptimiserRun,
    AgentPromptOptimiserRun,
    AgentPromptOptimiserRunStep,
    CallExecution,
    ComponentEvaluation,
    PromptTrial,
    RunTest,
    Scenarios,
    SimulateEvalConfig,
    TestExecution,
    TrialItemResult,
)

RUN_ID = "00000000-0000-4000-8000-000000001020"
TRIAL_ID = "00000000-0000-4000-8000-000000001021"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/simulate/api/agent-prompt-optimiser/", None),
        ("post", "/simulate/api/agent-prompt-optimiser/", {}),
        ("get", f"/simulate/api/agent-prompt-optimiser/{RUN_ID}/", None),
        ("put", f"/simulate/api/agent-prompt-optimiser/{RUN_ID}/", {}),
        ("patch", f"/simulate/api/agent-prompt-optimiser/{RUN_ID}/", {}),
        ("delete", f"/simulate/api/agent-prompt-optimiser/{RUN_ID}/", None),
        ("get", f"/simulate/api/agent-prompt-optimiser/{RUN_ID}/graph/", None),
        ("get", f"/simulate/api/agent-prompt-optimiser/{RUN_ID}/steps/", None),
        (
            "get",
            (
                f"/simulate/api/agent-prompt-optimiser/{RUN_ID}/trial/"
                f"{TRIAL_ID}/evaluations/"
            ),
            None,
        ),
        (
            "get",
            (f"/simulate/api/agent-prompt-optimiser/{RUN_ID}/trial/{TRIAL_ID}/prompt/"),
            None,
        ),
        (
            "get",
            (
                f"/simulate/api/agent-prompt-optimiser/{RUN_ID}/trial/"
                f"{TRIAL_ID}/scenarios/"
            ),
            None,
        ),
    ],
)
def test_agent_prompt_optimiser_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)

    if body is None:
        response = request(path)
    else:
        response = request(path, body, format="json")

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }


def _create_agent_prompt_optimiser_fixture(organization, workspace, prefix):
    agent_optimiser = AgentOptimiser.no_workspace_objects.create(
        name=f"{prefix} optimiser",
        configuration={"mode": "contract"},
    )
    agent_optimiser_run = AgentOptimiserRun.no_workspace_objects.create(
        agent_optimiser=agent_optimiser,
        status=AgentOptimiserRun.OptimiserStatus.COMPLETED,
        input_data={"prompt": "old"},
        result={"prompt": "new"},
        metadata={"source": prefix},
    )
    run_test = RunTest.no_workspace_objects.create(
        name=f"{prefix} run test",
        organization=organization,
        workspace=workspace,
    )
    test_execution = TestExecution.no_workspace_objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        completed_calls=1,
        agent_optimiser=agent_optimiser,
    )
    prompt_run = AgentPromptOptimiserRun.no_workspace_objects.create(
        name=f"{prefix} prompt optimiser",
        agent_optimiser=agent_optimiser,
        agent_optimiser_run=agent_optimiser_run,
        test_execution=test_execution,
        optimiser_type=AgentPromptOptimiserRun.OptimiserType.PROTEGI,
        model="gpt-4o-mini",
        status=AgentPromptOptimiserRun.Status.COMPLETED,
        result={"history": [{"trial": 1}]},
        configuration={
            "beam_size": 2,
            "num_gradients": 1,
            "errors_per_gradient": 1,
            "prompts_per_gradient": 1,
            "num_rounds": 1,
        },
    )
    AgentPromptOptimiserRunStep.no_workspace_objects.create(
        agent_prompt_optimiser_run=prompt_run,
        step_number=1,
        name="Collect calls",
        description="Collect call outputs",
        status=AgentPromptOptimiserRunStep.Status.COMPLETED,
        metadata={"count": 1},
    )
    AgentPromptOptimiserRunStep.no_workspace_objects.create(
        agent_prompt_optimiser_run=prompt_run,
        step_number=2,
        name="Generate prompt",
        description="Generate candidate prompt",
        status=AgentPromptOptimiserRunStep.Status.COMPLETED,
        metadata={"count": 1},
    )
    scenario = Scenarios.no_workspace_objects.create(
        name=f"{prefix} scenario",
        description="Handle a refund request",
        source="Customer asks for a refund.",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        status=StatusType.COMPLETED.value,
    )
    run_test.scenarios.add(scenario)
    call_execution = CallExecution.no_workspace_objects.create(
        test_execution=test_execution,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        scenario=scenario,
        phone_number="+15555550000",
        status=CallExecution.CallStatus.COMPLETED,
        call_metadata={"channel": "text"},
    )
    eval_template = EvalTemplate.no_workspace_objects.create(
        name=f"{prefix}-quality-eval",
        description="Scores whether the answer is helpful.",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="code",
        output_type_normalized=EvalTemplate.OutputTypeNormalized.PERCENTAGE,
        config={"required_keys": ["answer"]},
    )
    eval_config = SimulateEvalConfig.no_workspace_objects.create(
        run_test=run_test,
        eval_template=eval_template,
        name=f"{prefix}-quality",
        mapping={"answer": "output"},
        config={},
    )
    baseline_trial = PromptTrial.no_workspace_objects.create(
        agent_prompt_optimiser_run=prompt_run,
        trial_number=0,
        is_baseline=True,
        prompt="Base support prompt",
        average_score=0.4,
        metadata={"kind": "baseline"},
    )
    trial = PromptTrial.no_workspace_objects.create(
        agent_prompt_optimiser_run=prompt_run,
        trial_number=1,
        is_baseline=False,
        prompt="Improved support prompt",
        average_score=0.8,
        metadata={"kind": "candidate"},
    )
    baseline_item = TrialItemResult.no_workspace_objects.create(
        prompt_trial=baseline_trial,
        call_execution=call_execution,
        score=0.4,
        reason="Baseline answer was incomplete.",
        input_text="Can I get a refund?",
        output_text="Maybe.",
        metadata={"trial": 0},
    )
    trial_item = TrialItemResult.no_workspace_objects.create(
        prompt_trial=trial,
        call_execution=call_execution,
        score=0.8,
        reason="Candidate answer included next steps.",
        input_text="Can I get a refund?",
        output_text="I can help start the refund.",
        metadata={"trial": 1},
    )
    ComponentEvaluation.no_workspace_objects.create(
        trial_item_result=baseline_item,
        eval_config=eval_config,
        score=0.4,
        reason="Sparse answer.",
    )
    ComponentEvaluation.no_workspace_objects.create(
        trial_item_result=trial_item,
        eval_config=eval_config,
        score=0.8,
        reason="Helpful answer.",
    )

    return SimpleNamespace(
        run=prompt_run,
        test_execution=test_execution,
        trial=trial,
        baseline_trial=baseline_trial,
        eval_config=eval_config,
        trial_item=trial_item,
    )


@pytest.mark.django_db
def test_agent_prompt_optimiser_read_routes_scope_to_request_workspace(
    auth_client, organization, workspace, user, monkeypatch
):
    active = _create_agent_prompt_optimiser_fixture(organization, workspace, "active")
    other_workspace = Workspace.objects.create(
        name="Other Workspace",
        organization=organization,
        is_active=True,
        created_by=user,
    )
    hidden = _create_agent_prompt_optimiser_fixture(
        organization, other_workspace, "hidden"
    )

    monkeypatch.setattr(
        "simulate.views.agent_prompt_optimiser.get_provider_logo_url",
        lambda *_args, **_kwargs: "mock-logo",
    )

    list_response = auth_client.get(
        f"/simulate/api/agent-prompt-optimiser/?test_execution_id={active.test_execution.id}"
    )
    assert list_response.status_code == status.HTTP_200_OK
    list_result = list_response.data["result"]
    assert list_result["metadata"]["total_rows"] == 1
    assert [row["id"] for row in list_result["table"]] == [str(active.run.id)]

    hidden_list_response = auth_client.get(
        f"/simulate/api/agent-prompt-optimiser/?test_execution_id={hidden.test_execution.id}"
    )
    assert hidden_list_response.status_code == status.HTTP_200_OK
    assert hidden_list_response.data["result"]["metadata"]["total_rows"] == 0
    assert hidden_list_response.data["result"]["table"] == []

    detail_response = auth_client.get(
        f"/simulate/api/agent-prompt-optimiser/{active.run.id}/"
    )
    assert detail_response.status_code == status.HTTP_200_OK
    detail = detail_response.data["result"]
    assert detail["optimiser_name"] == active.run.name
    assert detail["provider_logo"] == "mock-logo"
    assert detail["table"][0]["id"] == str(active.trial.id)

    steps_response = auth_client.get(
        f"/simulate/api/agent-prompt-optimiser/{active.run.id}/steps/"
    )
    assert steps_response.status_code == status.HTTP_200_OK
    assert [row["step_number"] for row in steps_response.data["result"]] == [1, 2]

    graph_response = auth_client.get(
        f"/simulate/api/agent-prompt-optimiser/{active.run.id}/graph/"
    )
    assert graph_response.status_code == status.HTTP_200_OK
    graph_eval = graph_response.data["result"][str(active.eval_config.id)]
    assert [row["trial_number"] for row in graph_eval["evaluations"]] == [0, 1]

    prompt_response = auth_client.get(
        f"/simulate/api/agent-prompt-optimiser/{active.run.id}/trial/{active.trial.id}/prompt/"
    )
    assert prompt_response.status_code == status.HTTP_200_OK
    assert prompt_response.data["result"]["trial_prompt"] == "Improved support prompt"
    assert prompt_response.data["result"]["base_prompt"] == "Base support prompt"

    evaluations_response = auth_client.get(
        f"/simulate/api/agent-prompt-optimiser/{active.run.id}/trial/{active.trial.id}/evaluations/"
    )
    assert evaluations_response.status_code == status.HTTP_200_OK
    eval_row = evaluations_response.data["result"]["table"][0]
    assert eval_row["id"] == str(active.eval_config.id)
    assert eval_row["score"] == 0.8

    scenarios_response = auth_client.get(
        f"/simulate/api/agent-prompt-optimiser/{active.run.id}/trial/{active.trial.id}/scenarios/"
    )
    assert scenarios_response.status_code == status.HTTP_200_OK
    scenario_row = scenarios_response.data["result"]["table"][0]
    assert scenario_row["id"] == str(active.trial_item.id)
    assert scenario_row["output_text"] == "I can help start the refund."

    hidden_paths = [
        f"/simulate/api/agent-prompt-optimiser/{hidden.run.id}/",
        f"/simulate/api/agent-prompt-optimiser/{hidden.run.id}/steps/",
        f"/simulate/api/agent-prompt-optimiser/{hidden.run.id}/graph/",
        f"/simulate/api/agent-prompt-optimiser/{hidden.run.id}/trial/{hidden.trial.id}/prompt/",
        f"/simulate/api/agent-prompt-optimiser/{hidden.run.id}/trial/{hidden.trial.id}/evaluations/",
        f"/simulate/api/agent-prompt-optimiser/{hidden.run.id}/trial/{hidden.trial.id}/scenarios/",
    ]
    for path in hidden_paths:
        response = auth_client.get(path)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    hidden_detail_path = f"/simulate/api/agent-prompt-optimiser/{hidden.run.id}/"
    hidden_put_response = auth_client.put(hidden_detail_path, {}, format="json")
    hidden_patch_response = auth_client.patch(
        hidden_detail_path,
        {"status": AgentPromptOptimiserRun.Status.FAILED},
        format="json",
    )
    hidden_delete_response = auth_client.delete(hidden_detail_path)
    assert hidden_put_response.status_code == status.HTTP_404_NOT_FOUND
    assert hidden_patch_response.status_code == status.HTTP_404_NOT_FOUND
    assert hidden_delete_response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_agent_prompt_optimiser_create_scopes_test_execution_before_work(
    auth_client, organization, workspace, user, monkeypatch
):
    active = _create_agent_prompt_optimiser_fixture(
        organization, workspace, "create-active"
    )
    other_workspace = Workspace.objects.create(
        name="Create Hidden Workspace",
        organization=organization,
        is_active=True,
        created_by=user,
    )
    hidden = _create_agent_prompt_optimiser_fixture(
        organization, other_workspace, "create-hidden"
    )

    api_key_calls = []
    created_step_runs = []
    started_workflows = []

    def fake_get_api_key_for_model(**kwargs):
        api_key_calls.append(kwargs)
        return "test-api-key"

    monkeypatch.setattr(
        "simulate.serializers.agent_prompt_optimiser.get_api_key_for_model",
        fake_get_api_key_for_model,
    )
    monkeypatch.setattr(
        "simulate.views.agent_prompt_optimiser.create_agent_prompt_optimiser_run_steps",
        lambda run_id: created_step_runs.append(run_id),
    )
    monkeypatch.setattr(
        "simulate.views.agent_prompt_optimiser.start_agent_prompt_optimiser_workflow",
        lambda run_id: started_workflows.append(run_id),
    )

    payload = {
        "name": "Scoped prompt optimiser",
        "test_execution_id": str(active.test_execution.id),
        "optimiser_type": AgentPromptOptimiserRun.OptimiserType.PROTEGI,
        "model": "gpt-4o-mini",
        "configuration": {
            "beam_size": 2,
            "num_gradients": 1,
            "errors_per_gradient": 1,
            "prompts_per_gradient": 1,
            "num_rounds": 1,
        },
    }
    active_response = auth_client.post(
        "/simulate/api/agent-prompt-optimiser/",
        payload,
        format="json",
    )
    assert active_response.status_code == status.HTTP_201_CREATED
    created_run = AgentPromptOptimiserRun.no_workspace_objects.get(
        id=active_response.data["id"]
    )
    assert created_run.test_execution_id == active.test_execution.id
    assert created_step_runs == [str(created_run.id)]
    assert started_workflows == [str(created_run.id)]
    assert len(api_key_calls) == 1

    hidden_count = AgentPromptOptimiserRun.no_workspace_objects.filter(
        test_execution=hidden.test_execution
    ).count()
    hidden_payload = {**payload, "test_execution_id": str(hidden.test_execution.id)}
    hidden_response = auth_client.post(
        "/simulate/api/agent-prompt-optimiser/",
        hidden_payload,
        format="json",
    )
    assert hidden_response.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        AgentPromptOptimiserRun.no_workspace_objects.filter(
            test_execution=hidden.test_execution
        ).count()
        == hidden_count
    )
    assert created_step_runs == [str(created_run.id)]
    assert started_workflows == [str(created_run.id)]
    assert len(api_key_calls) == 1
