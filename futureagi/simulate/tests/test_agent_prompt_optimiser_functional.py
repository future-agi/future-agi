"""Functional CRUD + sub-resource + trial-route coverage for the agent prompt optimiser viewset."""

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


def _create_agent_prompt_optimiser_fixture(organization, workspace, prefix):
    agent_optimiser = AgentOptimiser.no_workspace_objects.create(
        name=f"{prefix} optimiser",
        configuration={"mode": "functional"},
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


@pytest.fixture
def active_fixture(organization, workspace):
    return _create_agent_prompt_optimiser_fixture(organization, workspace, "active")


@pytest.fixture
def hidden_fixture(organization, workspace, user):
    other_workspace = Workspace.objects.create(
        name="Hidden Workspace",
        organization=organization,
        is_active=True,
        created_by=user,
    )
    return _create_agent_prompt_optimiser_fixture(
        organization, other_workspace, "hidden"
    )


@pytest.fixture
def mock_provider_logo(monkeypatch):
    monkeypatch.setattr(
        "simulate.views.agent_prompt_optimiser.get_provider_logo_url",
        lambda *_args, **_kwargs: "mock-logo",
    )


@pytest.fixture
def mock_optimiser_side_effects(monkeypatch):
    calls = {"api_key": [], "steps": [], "workflow": []}

    def fake_get_api_key_for_model(**kwargs):
        calls["api_key"].append(kwargs)
        return "test-api-key"

    monkeypatch.setattr(
        "simulate.serializers.agent_prompt_optimiser.get_api_key_for_model",
        fake_get_api_key_for_model,
    )
    monkeypatch.setattr(
        "simulate.views.agent_prompt_optimiser.create_agent_prompt_optimiser_run_steps",
        lambda run_id: calls["steps"].append(run_id),
    )
    monkeypatch.setattr(
        "simulate.views.agent_prompt_optimiser.start_agent_prompt_optimiser_workflow",
        lambda run_id: calls["workflow"].append(run_id),
    )
    return calls


def _create_payload(test_execution_id):
    return {
        "name": "Functional prompt optimiser",
        "test_execution_id": str(test_execution_id),
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


MISSING_RUN_ID = "00000000-0000-4000-8000-0000000000ff"
MISSING_TRIAL_ID = "00000000-0000-4000-8000-0000000000fe"


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestAgentPromptOptimiserCRUD:
    def test_list_returns_only_active_workspace_rows(
        self, auth_client, active_fixture, hidden_fixture
    ):
        response = auth_client.get("/simulate/api/agent-prompt-optimiser/")

        assert response.status_code == status.HTTP_200_OK
        table = response.data["result"]["table"]
        listed_ids = {row["id"] for row in table}
        assert str(active_fixture.run.id) in listed_ids
        assert str(hidden_fixture.run.id) not in listed_ids
        active_row = next(row for row in table if row["id"] == str(active_fixture.run.id))
        assert active_row["optimisation_name"] == active_fixture.run.name
        assert active_row["model"] == "gpt-4o-mini"

    def test_list_excludes_cross_tenant_rows(
        self, auth_client, active_fixture, hidden_fixture
    ):
        response = auth_client.get(
            f"/simulate/api/agent-prompt-optimiser/?test_execution_id={hidden_fixture.test_execution.id}"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["metadata"]["total_rows"] == 0
        assert response.data["result"]["table"] == []

    def test_create_persists_run_scoped_to_workspace(
        self,
        auth_client,
        workspace,
        active_fixture,
        mock_optimiser_side_effects,
    ):
        response = auth_client.post(
            "/simulate/api/agent-prompt-optimiser/",
            _create_payload(active_fixture.test_execution.id),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        created_run = AgentPromptOptimiserRun.no_workspace_objects.get(
            id=response.data["id"]
        )
        assert created_run.test_execution_id == active_fixture.test_execution.id
        assert (
            created_run.test_execution.run_test.workspace_id == workspace.id
        )
        assert created_run.model == "gpt-4o-mini"
        assert mock_optimiser_side_effects["steps"] == [str(created_run.id)]
        assert mock_optimiser_side_effects["workflow"] == [str(created_run.id)]

    def test_create_rejects_cross_tenant_test_execution_before_workflow(
        self,
        auth_client,
        active_fixture,
        hidden_fixture,
        mock_optimiser_side_effects,
    ):
        baseline_count = AgentPromptOptimiserRun.no_workspace_objects.filter(
            test_execution=hidden_fixture.test_execution
        ).count()

        response = auth_client.post(
            "/simulate/api/agent-prompt-optimiser/",
            _create_payload(hidden_fixture.test_execution.id),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            AgentPromptOptimiserRun.no_workspace_objects.filter(
                test_execution=hidden_fixture.test_execution
            ).count()
            == baseline_count
        )
        assert mock_optimiser_side_effects["steps"] == []
        assert mock_optimiser_side_effects["workflow"] == []

    @pytest.mark.xfail(
        strict=True,
        reason="AgentPromptOptimiserRunCreateSerializer silently drops unknown fields; XPASSes when reject_unknown_fields is added.",
    )
    def test_create_ignores_unknown_body_field(
        self,
        auth_client,
        active_fixture,
        mock_optimiser_side_effects,
    ):
        payload = {**_create_payload(active_fixture.test_execution.id), "legacy_extra": "ignore me"}

        response = auth_client.post(
            "/simulate/api/agent-prompt-optimiser/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_retrieve_returns_full_detail_body(
        self, auth_client, active_fixture, mock_provider_logo
    ):
        response = auth_client.get(
            f"/simulate/api/agent-prompt-optimiser/{active_fixture.run.id}/"
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.data["result"]
        assert result["optimiser_name"] == active_fixture.run.name
        assert result["optimiser_type"] == AgentPromptOptimiserRun.OptimiserType.PROTEGI
        assert result["model"] == "gpt-4o-mini"
        assert result["provider_logo"] == "mock-logo"
        assert result["status"] == AgentPromptOptimiserRun.Status.COMPLETED
        trial_ids = {row["id"] for row in result["table"]}
        assert str(active_fixture.trial.id) in trial_ids

    def test_retrieve_missing_run_returns_404(self, auth_client):
        response = auth_client.get(
            f"/simulate/api/agent-prompt-optimiser/{MISSING_RUN_ID}/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_retrieve_cross_tenant_returns_404(self, auth_client, hidden_fixture):
        response = auth_client.get(
            f"/simulate/api/agent-prompt-optimiser/{hidden_fixture.run.id}/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_partial_update_persists_status_change(self, auth_client, active_fixture):
        response = auth_client.patch(
            f"/simulate/api/agent-prompt-optimiser/{active_fixture.run.id}/",
            {"status": AgentPromptOptimiserRun.Status.FAILED},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        active_fixture.run.refresh_from_db()
        assert active_fixture.run.status == AgentPromptOptimiserRun.Status.FAILED

    def test_update_missing_run_returns_404(self, auth_client):
        response = auth_client.patch(
            f"/simulate/api/agent-prompt-optimiser/{MISSING_RUN_ID}/",
            {"status": AgentPromptOptimiserRun.Status.FAILED},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_cross_tenant_leaves_target_unchanged(
        self, auth_client, hidden_fixture
    ):
        original_status = hidden_fixture.run.status

        put_response = auth_client.put(
            f"/simulate/api/agent-prompt-optimiser/{hidden_fixture.run.id}/",
            {},
            format="json",
        )
        patch_response = auth_client.patch(
            f"/simulate/api/agent-prompt-optimiser/{hidden_fixture.run.id}/",
            {"status": AgentPromptOptimiserRun.Status.FAILED},
            format="json",
        )

        assert put_response.status_code == status.HTTP_404_NOT_FOUND
        assert patch_response.status_code == status.HTTP_404_NOT_FOUND
        hidden_fixture.run.refresh_from_db()
        assert hidden_fixture.run.status == original_status

    def test_patch_partial_update_changes_only_specified_field(
        self, auth_client, active_fixture
    ):
        original_name = active_fixture.run.name
        original_model = active_fixture.run.model
        original_optimiser_type = active_fixture.run.optimiser_type
        original_configuration = active_fixture.run.configuration
        original_result = active_fixture.run.result

        response = auth_client.patch(
            f"/simulate/api/agent-prompt-optimiser/{active_fixture.run.id}/",
            {"status": AgentPromptOptimiserRun.Status.COMPLETED},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        active_fixture.run.refresh_from_db()
        assert active_fixture.run.status == AgentPromptOptimiserRun.Status.COMPLETED
        assert active_fixture.run.name == original_name
        assert active_fixture.run.model == original_model
        assert active_fixture.run.optimiser_type == original_optimiser_type
        assert active_fixture.run.configuration == original_configuration
        assert active_fixture.run.result == original_result

    def test_patch_partial_update_other_workspace_returns_404(
        self, auth_client, hidden_fixture
    ):
        original_status = hidden_fixture.run.status
        original_name = hidden_fixture.run.name

        response = auth_client.patch(
            f"/simulate/api/agent-prompt-optimiser/{hidden_fixture.run.id}/",
            {"status": AgentPromptOptimiserRun.Status.FAILED},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        hidden_fixture.run.refresh_from_db()
        assert hidden_fixture.run.status == original_status
        assert hidden_fixture.run.name == original_name

    def test_destroy_soft_deletes_target_run(self, auth_client, active_fixture):
        response = auth_client.delete(
            f"/simulate/api/agent-prompt-optimiser/{active_fixture.run.id}/"
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        active_fixture.run.refresh_from_db()
        assert active_fixture.run.deleted is True
        assert active_fixture.run.deleted_at is not None

    def test_destroy_missing_run_returns_404(self, auth_client):
        response = auth_client.delete(
            f"/simulate/api/agent-prompt-optimiser/{MISSING_RUN_ID}/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_destroy_cross_tenant_leaves_target_undeleted(
        self, auth_client, hidden_fixture
    ):
        response = auth_client.delete(
            f"/simulate/api/agent-prompt-optimiser/{hidden_fixture.run.id}/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        hidden_fixture.run.refresh_from_db()
        assert hidden_fixture.run.deleted is False
        assert hidden_fixture.run.deleted_at is None


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestAgentPromptOptimiserSubResources:
    def test_steps_returns_all_seeded_steps(self, auth_client, active_fixture):
        response = auth_client.get(
            f"/simulate/api/agent-prompt-optimiser/{active_fixture.run.id}/steps/"
        )

        assert response.status_code == status.HTTP_200_OK
        steps = response.data["result"]
        assert [row["step_number"] for row in steps] == [1, 2]
        assert steps[0]["name"] == "Collect calls"

    def test_steps_missing_run_returns_404(self, auth_client):
        response = auth_client.get(
            f"/simulate/api/agent-prompt-optimiser/{MISSING_RUN_ID}/steps/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_steps_cross_tenant_returns_404(self, auth_client, hidden_fixture):
        response = auth_client.get(
            f"/simulate/api/agent-prompt-optimiser/{hidden_fixture.run.id}/steps/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_graph_groups_evaluations_by_eval_config(
        self, auth_client, active_fixture
    ):
        response = auth_client.get(
            f"/simulate/api/agent-prompt-optimiser/{active_fixture.run.id}/graph/"
        )

        assert response.status_code == status.HTTP_200_OK
        graph = response.data["result"]
        series = graph[str(active_fixture.eval_config.id)]
        assert series["name"] == active_fixture.eval_config.name
        assert [row["trial_number"] for row in series["evaluations"]] == [0, 1]

    def test_graph_missing_run_returns_404(self, auth_client):
        response = auth_client.get(
            f"/simulate/api/agent-prompt-optimiser/{MISSING_RUN_ID}/graph/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_graph_cross_tenant_returns_404(self, auth_client, hidden_fixture):
        response = auth_client.get(
            f"/simulate/api/agent-prompt-optimiser/{hidden_fixture.run.id}/graph/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestAgentPromptOptimiserTrialRoutes:
    def test_trial_prompt_returns_candidate_and_baseline(
        self, auth_client, active_fixture
    ):
        response = auth_client.get(
            f"/simulate/api/agent-prompt-optimiser/{active_fixture.run.id}/"
            f"trial/{active_fixture.trial.id}/prompt/"
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.data["result"]
        assert result["trial_prompt"] == "Improved support prompt"
        assert result["base_prompt"] == "Base support prompt"
        assert result["trial_name"] == "Trial 1"
        assert result["optimisation_name"] == active_fixture.run.name

    def test_trial_prompt_unknown_trial_returns_400(
        self, auth_client, active_fixture
    ):
        # Product returns 400 (PROMPT_TRIAL_NOT_FOUND) rather than 404 for unknown trials.
        response = auth_client.get(
            f"/simulate/api/agent-prompt-optimiser/{active_fixture.run.id}/"
            f"trial/{MISSING_TRIAL_ID}/prompt/"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_trial_prompt_cross_tenant_returns_404(
        self, auth_client, hidden_fixture
    ):
        response = auth_client.get(
            f"/simulate/api/agent-prompt-optimiser/{hidden_fixture.run.id}/"
            f"trial/{hidden_fixture.trial.id}/prompt/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_trial_evaluations_returns_scored_rows(
        self, auth_client, active_fixture
    ):
        response = auth_client.get(
            f"/simulate/api/agent-prompt-optimiser/{active_fixture.run.id}/"
            f"trial/{active_fixture.trial.id}/evaluations/"
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.data["result"]
        eval_row = result["table"][0]
        assert eval_row["id"] == str(active_fixture.eval_config.id)
        assert eval_row["eval_name"] == active_fixture.eval_config.name
        assert eval_row["score"] == 0.8

    def test_trial_evaluations_unknown_trial_returns_400(
        self, auth_client, active_fixture
    ):
        # Product returns 400 (PROMPT_TRIAL_NOT_FOUND) rather than 404 for unknown trials.
        response = auth_client.get(
            f"/simulate/api/agent-prompt-optimiser/{active_fixture.run.id}/"
            f"trial/{MISSING_TRIAL_ID}/evaluations/"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_trial_evaluations_cross_tenant_returns_404(
        self, auth_client, hidden_fixture
    ):
        response = auth_client.get(
            f"/simulate/api/agent-prompt-optimiser/{hidden_fixture.run.id}/"
            f"trial/{hidden_fixture.trial.id}/evaluations/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_trial_scenarios_returns_trial_item_rows(
        self, auth_client, active_fixture
    ):
        response = auth_client.get(
            f"/simulate/api/agent-prompt-optimiser/{active_fixture.run.id}/"
            f"trial/{active_fixture.trial.id}/scenarios/"
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.data["result"]
        scenario_row = result["table"][0]
        assert scenario_row["id"] == str(active_fixture.trial_item.id)
        assert scenario_row["output_text"] == "I can help start the refund."
        assert scenario_row["input_text"] == "Can I get a refund?"

    def test_trial_scenarios_unknown_trial_returns_400(
        self, auth_client, active_fixture
    ):
        # Product returns 400 (PROMPT_TRIAL_NOT_FOUND) rather than 404 for unknown trials.
        response = auth_client.get(
            f"/simulate/api/agent-prompt-optimiser/{active_fixture.run.id}/"
            f"trial/{MISSING_TRIAL_ID}/scenarios/"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_trial_scenarios_cross_tenant_returns_404(
        self, auth_client, hidden_fixture
    ):
        response = auth_client.get(
            f"/simulate/api/agent-prompt-optimiser/{hidden_fixture.run.id}/"
            f"trial/{hidden_fixture.trial.id}/scenarios/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
