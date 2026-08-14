import uuid

import pytest
from rest_framework.test import APIClient

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.choices import DatasetSourceChoices, ModelTypes, SourceChoices
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.experiments import ExperimentsTable
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import set_workspace_context


def _payload_rows(response):
    payload = response.json()
    return payload.get("results", payload) if isinstance(payload, dict) else payload


@pytest.fixture
def experiment_detail_context():
    organization = Organization.objects.create(name="Experiment Detail Org")
    user = User.objects.create_user(
        email="experiment-detail@example.com",
        password="testpassword123",
        name="Experiment Detail User",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
    )
    active_workspace = Workspace.objects.create(
        name="Active Experiment Workspace",
        organization=organization,
        is_default=False,
        created_by=user,
    )
    other_workspace = Workspace.objects.create(
        name="Other Experiment Workspace",
        organization=organization,
        is_default=False,
        created_by=user,
    )
    set_workspace_context(workspace=active_workspace, organization=organization)

    def make_dataset(name, workspace):
        dataset = Dataset.no_workspace_objects.create(
            name=name,
            organization=organization,
            user=user,
            source=DatasetSourceChoices.BUILD.value,
            model_type=ModelTypes.GENERATIVE_LLM.value,
            workspace=workspace,
            column_order=[],
            column_config={},
        )
        column = Column.objects.create(
            name=f"{name} output",
            data_type="text",
            source=SourceChoices.RUN_PROMPT.value,
            dataset=dataset,
        )
        dataset.column_order = [str(column.id)]
        dataset.column_config = {str(column.id): {"is_visible": True}}
        dataset.save(update_fields=["column_order", "column_config"])
        return dataset, column

    active_dataset, active_column = make_dataset(
        "Active experiment detail dataset", active_workspace
    )
    other_dataset, other_column = make_dataset(
        "Other workspace experiment detail dataset", other_workspace
    )

    active_experiment = ExperimentsTable.objects.create(
        name="visible active experiment detail",
        dataset=active_dataset,
        column=active_column,
        prompt_config=[],
        experiment_type="llm",
    )
    other_experiment = ExperimentsTable.no_workspace_objects.create(
        name="hidden other workspace experiment detail",
        dataset=other_dataset,
        column=other_column,
        prompt_config=[],
        experiment_type="llm",
    )

    client = APIClient()
    client.force_authenticate(user=user)
    client.credentials(HTTP_X_WORKSPACE_ID=str(active_workspace.id))

    return {
        "client": client,
        "organization": organization,
        "active_workspace": active_workspace,
        "other_workspace": other_workspace,
        "active_dataset": active_dataset,
        "other_dataset": other_dataset,
        "active_experiment": active_experiment,
        "other_experiment": other_experiment,
    }


@pytest.mark.django_db
def test_experiment_detail_list_is_scoped_to_active_workspace(
    experiment_detail_context,
):
    response = experiment_detail_context["client"].get("/model-hub/experiment-detail/")

    assert response.status_code == 200
    ids = {row["id"] for row in _payload_rows(response)}
    assert str(experiment_detail_context["active_experiment"].id) in ids
    assert str(experiment_detail_context["other_experiment"].id) not in ids


@pytest.mark.django_db
def test_experiment_detail_dataset_filter_hides_other_workspace_dataset(
    experiment_detail_context,
):
    client = experiment_detail_context["client"]

    active_response = client.get(
        "/model-hub/experiment-detail/",
        {"dataset_id": str(experiment_detail_context["active_dataset"].id)},
    )
    assert active_response.status_code == 200
    active_ids = {row["id"] for row in _payload_rows(active_response)}
    assert str(experiment_detail_context["active_experiment"].id) in active_ids

    other_response = client.get(
        "/model-hub/experiment-detail/",
        {"dataset_id": str(experiment_detail_context["other_dataset"].id)},
    )
    assert other_response.status_code == 200
    other_payload = _payload_rows(other_response)
    assert other_payload == []
    assert str(experiment_detail_context["other_experiment"].id) not in {
        row.get("id") for row in other_payload
    }


@pytest.mark.django_db
def test_experiment_detail_search_does_not_leak_other_workspace_name(
    experiment_detail_context,
):
    response = experiment_detail_context["client"].get(
        "/model-hub/experiment-detail/",
        {"search": experiment_detail_context["other_experiment"].name},
    )

    assert response.status_code == 200
    assert _payload_rows(response) == []
