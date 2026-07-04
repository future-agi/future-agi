import pytest
from rest_framework import status

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.dataset_optimization_step import DatasetOptimizationStep
from model_hub.models.dataset_optimization_trial import DatasetOptimizationTrial
from model_hub.models.dataset_optimization_trial_item import (
    DatasetOptimizationItemEvaluation,
    DatasetOptimizationTrialItem,
)
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.optimize_dataset import OptimizeDataset
from model_hub.serializers.dataset_optimization import (
    DatasetOptimizationCreateSerializer,
    DatasetOptimizationListSerializer,
)


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="Dataset Optimization Org")


@pytest.fixture
def user(db, organization):
    return User.objects.create_user(
        email="dataset-opt@example.com",
        password="testpassword123",
        name="Dataset Opt User",
        organization=organization,
    )


@pytest.fixture
def workspace(db, organization, user):
    return Workspace.objects.create(
        name="Default Workspace",
        organization=organization,
        is_default=True,
        created_by=user,
    )


@pytest.fixture
def auth_client(user, workspace, monkeypatch):
    from conftest import WorkspaceAwareAPIClient

    monkeypatch.setattr(
        "tfc.ee_gating.check_ee_feature",
        lambda *args, **kwargs: None,
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    yield client
    client.stop_workspace_injection()


@pytest.fixture
def dataset(db, organization, workspace):
    return Dataset.objects.create(
        name="Dataset Optimization Dataset",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def output_column(db, dataset):
    return Column.objects.create(
        name="Prompt Output",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )


@pytest.fixture
def ai_model(db, organization, workspace):
    return AIModel.objects.create(
        user_model_id="gpt-4o-mini",
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def eval_template(db, organization, workspace):
    return EvalTemplate.objects.create(
        name="dataset-optimization-eval-template",
        organization=organization,
        workspace=workspace,
        criteria="Evaluate {{output}}",
        model="gpt-4o-mini",
    )


@pytest.fixture
def user_eval_metric(db, organization, workspace, dataset, eval_template):
    return UserEvalMetric.no_workspace_objects.create(
        name="Dataset Optimization Metric",
        organization=organization,
        workspace=workspace,
        template=eval_template,
        dataset=dataset,
        config={"mapping": {"output": "output"}},
    )


def create_optimization_run(column, **overrides):
    data = {
        "name": "Optimization Run",
        "column": column,
        "optimizer_algorithm": OptimizeDataset.OptimizerAlgorithm.RANDOM_SEARCH,
        "optimizer_config": {"num_variations": 1},
        "status": OptimizeDataset.StatusType.PENDING,
        "optimize_type": OptimizeDataset.OptimizeType.TEMPLATE,
        "environment": OptimizeDataset.EnvTypes.TRAINING,
        "version": "1.0",
    }
    data.update(overrides)
    return OptimizeDataset.objects.create(**data)


@pytest.mark.django_db
def test_dataset_optimization_create_uses_ai_model_user_model_id(
    output_column, ai_model
):
    serializer = DatasetOptimizationCreateSerializer(
        data={
            "name": "Optimization Run",
            "column_id": str(output_column.id),
            "optimizer_algorithm": OptimizeDataset.OptimizerAlgorithm.RANDOM_SEARCH,
            "optimizer_model_id": "gpt-4o-mini",
            "optimizer_config": {"num_variations": 1},
            "user_eval_template_ids": [],
        }
    )

    assert serializer.is_valid(), serializer.errors
    run = serializer.save()

    assert run.optimizer_model == ai_model
    assert run.optimizer_config["model_name"] == "gpt-4o-mini"


@pytest.mark.django_db
def test_dataset_optimization_list_returns_user_model_id(output_column, ai_model):
    run = OptimizeDataset.objects.create(
        name="Optimization Run",
        column=output_column,
        optimizer_model=ai_model,
        optimizer_algorithm=OptimizeDataset.OptimizerAlgorithm.RANDOM_SEARCH,
        optimizer_config={"num_variations": 1},
        status=OptimizeDataset.StatusType.PENDING,
        optimize_type=OptimizeDataset.OptimizeType.TEMPLATE,
        environment=OptimizeDataset.EnvTypes.TRAINING,
        version="1.0",
    )

    data = DatasetOptimizationListSerializer(run).data

    assert data["optimizer_model_id"] == "gpt-4o-mini"


@pytest.mark.django_db
class TestDatasetOptimizationWorkspaceIsolation:
    def test_list_and_actions_reject_other_workspace_runs(
        self, auth_client, organization, user, workspace, output_column
    ):
        visible_run = create_optimization_run(output_column, name="Visible run")
        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=organization,
            created_by=user,
        )
        other_dataset = Dataset.objects.create(
            name="Other Workspace Dataset",
            organization=organization,
            workspace=other_workspace,
        )
        other_column = Column.objects.create(
            name="Other Output",
            dataset=other_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
        )
        other_run = create_optimization_run(
            other_column,
            name="Other workspace run",
        )
        other_trial = DatasetOptimizationTrial.objects.create(
            optimization_run=other_run,
            trial_number=1,
            average_score=0.5,
            prompt="Other prompt",
        )

        list_response = auth_client.get("/model-hub/dataset-optimization/")

        assert list_response.status_code == status.HTTP_200_OK
        ids = {
            row["id"]
            for row in list_response.json()["result"]["table"]
            if row.get("id")
        }
        assert str(visible_run.id) in ids
        assert str(other_run.id) not in ids

        guarded_paths = [
            ("get", f"/model-hub/dataset-optimization/{other_run.id}/"),
            ("get", f"/model-hub/dataset-optimization/{other_run.id}/steps/"),
            ("get", f"/model-hub/dataset-optimization/{other_run.id}/graph/"),
            (
                "get",
                f"/model-hub/dataset-optimization/{other_run.id}/trial/{other_trial.id}/",
            ),
            (
                "get",
                f"/model-hub/dataset-optimization/{other_run.id}/trial/{other_trial.id}/prompt/",
            ),
            (
                "get",
                f"/model-hub/dataset-optimization/{other_run.id}/trial/{other_trial.id}/scenarios/",
            ),
            (
                "get",
                f"/model-hub/dataset-optimization/{other_run.id}/trial/{other_trial.id}/evaluations/",
            ),
            ("post", f"/model-hub/dataset-optimization/{other_run.id}/stop/"),
        ]
        for method, path in guarded_paths:
            response = getattr(auth_client, method)(path, {})
            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_create_rejects_other_workspace_column(
        self, auth_client, organization, user
    ):
        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=organization,
            created_by=user,
        )
        other_dataset = Dataset.objects.create(
            name="Other Workspace Dataset",
            organization=organization,
            workspace=other_workspace,
        )
        other_column = Column.objects.create(
            name="Other Output",
            dataset=other_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
        )

        response = auth_client.post(
            "/model-hub/dataset-optimization/",
            {
                "name": "Blocked optimization",
                "column_id": str(other_column.id),
                "optimizer_algorithm": OptimizeDataset.OptimizerAlgorithm.RANDOM_SEARCH,
                "optimizer_config": {"num_variations": 1},
                "user_eval_template_ids": [],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not OptimizeDataset.objects.filter(name="Blocked optimization").exists()

    def test_create_rejects_other_workspace_eval_metric(
        self, auth_client, organization, user, output_column
    ):
        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=organization,
            created_by=user,
        )
        other_dataset = Dataset.objects.create(
            name="Other Workspace Dataset",
            organization=organization,
            workspace=other_workspace,
        )
        template = EvalTemplate.objects.create(
            name="Other workspace eval template",
            organization=organization,
            workspace=other_workspace,
            criteria="Evaluate {{output}}",
            model="gpt-4o-mini",
        )
        other_metric = UserEvalMetric.no_workspace_objects.create(
            name="Other workspace metric",
            organization=organization,
            workspace=other_workspace,
            template=template,
            dataset=other_dataset,
            config={"mapping": {"output": "output"}},
        )

        response = auth_client.post(
            "/model-hub/dataset-optimization/",
            {
                "name": "Blocked metric optimization",
                "column_id": str(output_column.id),
                "optimizer_algorithm": OptimizeDataset.OptimizerAlgorithm.RANDOM_SEARCH,
                "optimizer_config": {"num_variations": 1},
                "user_eval_template_ids": [str(other_metric.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not OptimizeDataset.objects.filter(
            name="Blocked metric optimization"
        ).exists()

    def test_patch_rejects_other_workspace_column(
        self, auth_client, organization, user, output_column
    ):
        run = create_optimization_run(output_column)
        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=organization,
            created_by=user,
        )
        other_dataset = Dataset.objects.create(
            name="Other Workspace Dataset",
            organization=organization,
            workspace=other_workspace,
        )
        other_column = Column.objects.create(
            name="Other Output",
            dataset=other_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
        )

        response = auth_client.patch(
            f"/model-hub/dataset-optimization/{run.id}/",
            {"column": str(other_column.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        run.refresh_from_db()
        assert run.column_id == output_column.id

    def test_delete_soft_deletes_child_steps_trials_items_and_evaluations(
        self, auth_client, output_column, user_eval_metric
    ):
        run = create_optimization_run(output_column)
        step = DatasetOptimizationStep.objects.create(
            optimization_run=run,
            step_number=1,
            name="Generate candidates",
            status=DatasetOptimizationStep.Status.COMPLETED,
        )
        trial = DatasetOptimizationTrial.objects.create(
            optimization_run=run,
            trial_number=1,
            average_score=0.8,
            prompt="Optimized prompt",
        )
        item = DatasetOptimizationTrialItem.objects.create(
            trial=trial,
            row_id="row-1",
            score=0.8,
            input_text="input",
            output_text="output",
        )
        evaluation = DatasetOptimizationItemEvaluation.objects.create(
            trial_item=item,
            eval_metric=user_eval_metric,
            score=0.8,
        )

        response = auth_client.delete(f"/model-hub/dataset-optimization/{run.id}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        for model, pk in [
            (OptimizeDataset, run.id),
            (DatasetOptimizationStep, step.id),
            (DatasetOptimizationTrial, trial.id),
            (DatasetOptimizationTrialItem, item.id),
            (DatasetOptimizationItemEvaluation, evaluation.id),
        ]:
            obj = model.all_objects.get(id=pk)
            assert obj.deleted is True
            assert obj.deleted_at is not None


EXPECTED_RETRIEVE_KEYS = {
    "optimiser_name",
    "optimiser_type",
    "model",
    "provider_logo",
    "configuration",
    "status",
    "error_message",
    "start_time",
    "parameters",
    "column_id",
    "column_name",
    "best_score",
    "baseline_score",
    "table",
    "column_config",
    "optimizer_model_id",
    "user_eval_templates",
}


@pytest.mark.django_db
def test_retrieve_returns_documented_shape(
    auth_client, output_column, ai_model, user_eval_metric
):
    run = create_optimization_run(
        output_column,
        optimizer_model=ai_model,
        best_score=0.87,
        baseline_score=0.5,
    )
    run.user_eval_template_ids.set([user_eval_metric])

    response = auth_client.get(f"/model-hub/dataset-optimization/{run.id}/")

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]

    assert set(result.keys()) == EXPECTED_RETRIEVE_KEYS

    assert result["optimiser_name"] == "Optimization Run"
    assert result["optimiser_type"] == OptimizeDataset.OptimizerAlgorithm.RANDOM_SEARCH
    assert result["model"] == "gpt-4o-mini"
    assert result["optimizer_model_id"] == "gpt-4o-mini"
    assert result["column_id"] == str(output_column.id)
    assert result["column_name"] == output_column.name
    assert result["best_score"] == 0.87
    assert result["baseline_score"] == 0.5
    assert result["status"] == OptimizeDataset.StatusType.PENDING
    assert result["configuration"] == {"num_variations": 1}

    assert isinstance(result["table"], list)
    assert isinstance(result["column_config"], list)
    assert isinstance(result["parameters"], list)
    assert isinstance(result["user_eval_templates"], list)

    assert len(result["user_eval_templates"]) == 1
    eval_row = result["user_eval_templates"][0]
    assert eval_row["id"] == str(user_eval_metric.id)
    assert eval_row["eval_id"] == str(user_eval_metric.id)
    assert eval_row["template_id"] == str(user_eval_metric.template.id)

    params_by_key = {p["key"]: p for p in result["parameters"]}
    assert "num_variations" in params_by_key
    assert params_by_key["num_variations"]["label"] == "Number of Variations"
    assert params_by_key["num_variations"]["value"] == 1
    assert "model_name" not in params_by_key


@pytest.mark.django_db
def test_retrieve_falls_back_to_config_model_name(auth_client, output_column):
    run = create_optimization_run(
        output_column,
        optimizer_config={"num_variations": 1, "model_name": "gpt-4o"},
    )

    response = auth_client.get(f"/model-hub/dataset-optimization/{run.id}/")

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    assert result["model"] == "gpt-4o"
    assert result["optimizer_model_id"] == "gpt-4o"
    assert result["provider_logo"] is None or isinstance(result["provider_logo"], str)


@pytest.mark.django_db
def test_retrieve_handles_run_without_model_or_evals(auth_client, output_column):
    run = create_optimization_run(output_column)

    response = auth_client.get(f"/model-hub/dataset-optimization/{run.id}/")

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    assert result["model"] is None
    assert result["optimizer_model_id"] is None
    assert result["provider_logo"] is None
    assert result["user_eval_templates"] == []
    assert result["table"] == []


@pytest.mark.django_db
def test_retrieve_table_row_shape_for_trial_without_baseline(
    auth_client, output_column, ai_model, user_eval_metric
):
    """Regression: non-baseline trial with no baseline present must serialize
    with score_percentage_change=None and eval_scores as a keyed mapping.
    """
    run = create_optimization_run(
        output_column,
        optimizer_model=ai_model,
    )
    run.user_eval_template_ids.set([user_eval_metric])
    trial = DatasetOptimizationTrial.objects.create(
        optimization_run=run,
        trial_number=1,
        is_baseline=False,
        prompt="candidate prompt",
        average_score=0.75,
    )
    item = DatasetOptimizationTrialItem.objects.create(
        trial=trial,
        row_id="row-1",
        score=0.75,
        reason="",
    )
    DatasetOptimizationItemEvaluation.objects.create(
        trial_item=item,
        eval_metric=user_eval_metric,
        score=0.75,
        reason="",
    )

    response = auth_client.get(f"/model-hub/dataset-optimization/{run.id}/")
    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    assert len(result["table"]) == 1
    row = result["table"][0]
    assert row["score_percentage_change"] is None
    assert row["is_best"] is True
    assert isinstance(row["eval_scores"], dict)
    metric_id = str(user_eval_metric.id)
    assert metric_id in row["eval_scores"]
    assert row["eval_scores"][metric_id]["score"] == 0.75
    assert row["eval_scores"][metric_id]["percentage_change"] is None
