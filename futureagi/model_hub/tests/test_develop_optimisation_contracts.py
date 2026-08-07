import uuid

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.choices import (
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.develop_optimisation import OptimizationDataset
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric


def response_rows(response):
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    return body.get("results", body.get("result", []))


def assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["details"][field_name] == ["Unknown field."]


def create_dataset_with_column(organization, workspace, user, name=None):
    dataset = Dataset.no_workspace_objects.create(
        name=name or f"Optimization Dataset {uuid.uuid4()}",
        organization=organization,
        workspace=workspace,
        user=user,
    )
    column = Column.no_workspace_objects.create(
        name="Prompt Output",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(column.id)]
    dataset.save()
    return dataset, column


def create_eval_metric(organization, workspace, dataset):
    suffix = uuid.uuid4().hex[:8]
    template = EvalTemplate.no_workspace_objects.create(
        name=f"optimization-eval-template-{suffix}",
        organization=organization,
        workspace=workspace,
        config={"output": "Pass/Fail", "eval_type_id": "CustomCodeEval"},
        criteria="Return pass when the answer is useful.",
    )
    return UserEvalMetric.no_workspace_objects.create(
        name=f"optimization-eval-{suffix}",
        organization=organization,
        workspace=workspace,
        template=template,
        dataset=dataset,
        config={"mapping": {"output": str(dataset.column_order[0])}},
        status=StatusType.COMPLETED.value,
    )


def optimization_payload(dataset, column, metric, name=None):
    return {
        "name": name or f"Optimization {uuid.uuid4()}",
        "dataset_id": str(dataset.id),
        "column_id": str(column.id),
        "messages": [{"role": "user", "content": "Answer {{input}}"}],
        "user_eval_template_ids": [str(metric.id)],
        "model_config": {
            "model_name": "gpt-4o-mini",
            "temperature": 0,
            "max_tokens": 100,
            "top_p": 1,
        },
        "optimize_type": "PROMPT_TEMPLATE",
        "user_eval_template_mapping": {},
        "prompt_name": "Prompt",
    }


def create_optimization(dataset, column, metric, name=None):
    optimization = OptimizationDataset.no_workspace_objects.create(
        name=name or f"Optimization {uuid.uuid4()}",
        optimize_type="PROMPT_TEMPLATE",
        dataset=dataset,
        column=column,
        messages=[{"role": "user", "content": "Answer {{input}}"}],
        model_config={
            "model_name": "gpt-4o-mini",
            "temperature": 0,
            "max_tokens": 100,
            "top_p": 1,
        },
        user_eval_template_mapping={},
        prompt_name="Prompt",
        optimized_k_prompts=["Answer {{input}} well"],
    )
    optimization.user_eval_template_ids.set([metric])
    return optimization


def create_other_org_workspace_user():
    other_org = Organization.objects.create(name=f"Other Org {uuid.uuid4()}")
    other_user = User.objects.create_user(
        email=f"other-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Other User",
        organization=other_org,
    )
    other_workspace = Workspace.no_workspace_objects.create(
        name=f"Other Workspace {uuid.uuid4()}",
        organization=other_org,
        created_by=other_user,
    )
    return other_org, other_workspace, other_user


@pytest.mark.django_db
def test_optimisation_create_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        "/model-hub/optimisation/create/",
        {
            "name": "Optimise prompt",
            "dataset_id": str(uuid.uuid4()),
            "messages": [{"role": "user", "content": "Answer"}],
            "user_eval_template_ids": [],
            "model_config": {},
            "optimize_type": "prompt",
            "datasetId": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "datasetId")


@pytest.mark.django_db
def test_optimisation_authenticated_lifecycle_scopes_dataset_rows(
    auth_client, organization, workspace, user, monkeypatch
):
    dataset, column = create_dataset_with_column(organization, workspace, user)
    metric = create_eval_metric(organization, workspace, dataset)
    monkeypatch.setattr(
        "model_hub.views.develop_optimisation.DevelopOptimizer.create_column",
        lambda self: None,
    )

    create_response = auth_client.post(
        "/model-hub/optimisation/create/",
        optimization_payload(dataset, column, metric, name="Lifecycle Optimization"),
        format="json",
    )

    assert create_response.status_code == status.HTTP_200_OK
    optimization = OptimizationDataset.no_workspace_objects.get(
        name="Lifecycle Optimization",
        dataset=dataset,
        deleted=False,
    )
    assert optimization.column_id == column.id
    assert list(optimization.user_eval_template_ids.values_list("id", flat=True)) == [
        metric.id
    ]

    list_response = auth_client.get(
        "/model-hub/optimisation/",
        {"dataset_id": str(dataset.id), "limit": 20},
    )
    rows = response_rows(list_response)
    assert any(row["id"] == str(optimization.id) for row in rows)

    detail_response = auth_client.get(f"/model-hub/optimisation/{optimization.id}/")
    assert detail_response.status_code == status.HTTP_200_OK
    assert detail_response.json()["id"] == str(optimization.id)

    details_response = auth_client.get(
        f"/model-hub/optimisation/{optimization.id}/details/"
    )
    assert details_response.status_code == status.HTTP_200_OK
    assert details_response.json()["id"] == str(optimization.id)
    assert details_response.json()["user_eval_template_ids"] == [str(metric.id)]

    update_response = auth_client.put(
        f"/model-hub/optimisation/update/{optimization.id}/",
        {"name": "Lifecycle Optimization Updated"},
        format="json",
    )
    assert update_response.status_code == status.HTTP_200_OK
    optimization.refresh_from_db()
    assert optimization.name == "Lifecycle Optimization Updated"


@pytest.mark.django_db
def test_optimisation_routes_reject_same_org_other_workspace_and_cross_org_rows(
    auth_client, organization, workspace, user
):
    visible_dataset, visible_column = create_dataset_with_column(
        organization, workspace, user, name="Visible Optimization Dataset"
    )
    visible_metric = create_eval_metric(organization, workspace, visible_dataset)
    visible_optimization = create_optimization(
        visible_dataset,
        visible_column,
        visible_metric,
        name="Visible Optimization",
    )

    other_workspace = Workspace.no_workspace_objects.create(
        name=f"Other Same Org Workspace {uuid.uuid4()}",
        organization=organization,
        created_by=user,
    )
    other_dataset, other_column = create_dataset_with_column(
        organization, other_workspace, user, name="Other Workspace Dataset"
    )
    other_metric = create_eval_metric(organization, other_workspace, other_dataset)
    other_optimization = create_optimization(
        other_dataset,
        other_column,
        other_metric,
        name="Other Workspace Optimization",
    )

    other_org, cross_workspace, cross_user = create_other_org_workspace_user()
    cross_dataset, cross_column = create_dataset_with_column(
        other_org, cross_workspace, cross_user, name="Cross Org Dataset"
    )
    cross_metric = create_eval_metric(other_org, cross_workspace, cross_dataset)
    cross_optimization = create_optimization(
        cross_dataset,
        cross_column,
        cross_metric,
        name="Cross Org Optimization",
    )

    list_response = auth_client.get("/model-hub/optimisation/", {"limit": 50})
    rows = response_rows(list_response)
    row_ids = {row["id"] for row in rows}
    assert str(visible_optimization.id) in row_ids
    assert str(other_optimization.id) not in row_ids
    assert str(cross_optimization.id) not in row_ids

    for optimization in (other_optimization, cross_optimization):
        detail_response = auth_client.get(f"/model-hub/optimisation/{optimization.id}/")
        details_response = auth_client.get(
            f"/model-hub/optimisation/{optimization.id}/details/"
        )
        update_response = auth_client.put(
            f"/model-hub/optimisation/update/{optimization.id}/",
            {"name": "Should Not Update"},
            format="json",
        )

        assert detail_response.status_code == status.HTTP_404_NOT_FOUND
        assert details_response.status_code == status.HTTP_404_NOT_FOUND
        assert update_response.status_code == status.HTTP_404_NOT_FOUND
        optimization.refresh_from_db()
        assert optimization.name != "Should Not Update"


@pytest.mark.django_db
def test_optimisation_create_and_update_reject_columns_outside_selected_dataset(
    auth_client, organization, workspace, user, monkeypatch
):
    dataset, column = create_dataset_with_column(organization, workspace, user)
    metric = create_eval_metric(organization, workspace, dataset)
    existing = create_optimization(
        dataset, column, metric, name="Existing Optimization"
    )

    other_dataset, other_column = create_dataset_with_column(
        organization, workspace, user, name="Other Column Dataset"
    )
    other_metric = create_eval_metric(organization, workspace, other_dataset)
    monkeypatch.setattr(
        "model_hub.views.develop_optimisation.DevelopOptimizer.create_column",
        lambda self: None,
    )

    create_response = auth_client.post(
        "/model-hub/optimisation/create/",
        optimization_payload(
            dataset,
            other_column,
            metric,
            name="Invalid Column Optimization",
        ),
        format="json",
    )
    update_response = auth_client.put(
        f"/model-hub/optimisation/update/{existing.id}/",
        {"column_id": str(other_column.id)},
        format="json",
    )
    metric_response = auth_client.put(
        f"/model-hub/optimisation/update/{existing.id}/",
        {"user_eval_template_ids": [str(other_metric.id)]},
        format="json",
    )

    assert create_response.status_code == status.HTTP_400_BAD_REQUEST
    assert update_response.status_code == status.HTTP_400_BAD_REQUEST
    assert metric_response.status_code == status.HTTP_400_BAD_REQUEST
    assert not OptimizationDataset.no_workspace_objects.filter(
        name="Invalid Column Optimization",
        deleted=False,
    ).exists()
    existing.refresh_from_db()
    assert existing.column_id == column.id


@pytest.mark.django_db
def test_optimisation_update_rejects_unknown_request_fields(auth_client):
    response = auth_client.put(
        f"/model-hub/optimisation/update/{uuid.uuid4()}/",
        {"datasetId": "legacy camel alias"},
        format="json",
    )

    assert_unknown_field(response, "datasetId")
