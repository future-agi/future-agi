import pytest

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Column, Dataset
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
