"""edit_dataset_behavior keeps what a save does not name, and stays in its org.

DSX launch lane L4. A dataset settings save (``PUT edit_dataset_behavior``)
used to replace ``dataset_config`` with ``{dismiss_banner}`` and load the
dataset without an organization filter. These tests pin the merge and prove,
with two organizations, that tenant B can neither read nor change tenant A's
dataset through this endpoint.
"""

from __future__ import annotations

import uuid

import pytest
from rest_framework import status

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Column, Dataset
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles


def _behavior_url(dataset_id):
    return f"/model-hub/develops/{dataset_id}/edit_dataset_behavior/"


def _second_tenant(label="Tenant B"):
    """A separate organization with its own owner, workspace and API client."""
    from conftest import WorkspaceAwareAPIClient

    suffix = uuid.uuid4().hex[:8]
    org = Organization.objects.create(name=f"{label} {suffix}")
    user = User.objects.create_user(
        email=f"l4-b-{suffix}@futureagi.com",
        password="testpassword123",
        name=f"{label} Owner",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=org,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    workspace = Workspace.no_workspace_objects.create(
        name=f"{label} Workspace {suffix}",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "role": "Workspace Owner",
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    return org, workspace, client


@pytest.fixture
def tenant_b(db):
    org, workspace, client = _second_tenant()
    yield org, workspace, client
    client.stop_workspace_injection()


@pytest.fixture
def dataset_a(organization, workspace):
    dataset = Dataset.no_workspace_objects.create(
        name=f"l4-a-{uuid.uuid4().hex[:6]}",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
        dataset_config={
            "eval_recommendations": ["groundedness"],
            "environment_labels": ["prod"],
        },
    )
    question = Column.no_workspace_objects.create(
        name="question",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(question.id)]
    dataset.column_config = {
        str(question.id): {"is_visible": True, "is_frozen": None, "role": "input"}
    }
    dataset.save()
    return dataset, question


@pytest.mark.django_db
def test_dismiss_banner_keeps_the_other_dataset_settings(auth_client, dataset_a):
    dataset, _question = dataset_a

    response = auth_client.put(
        _behavior_url(dataset.id),
        {"dataset_config": {"dismiss_banner": True}},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    dataset.refresh_from_db()
    assert dataset.dataset_config == {
        "eval_recommendations": ["groundedness"],
        "environment_labels": ["prod"],
        "dismiss_banner": True,
    }


@pytest.mark.django_db
def test_a_save_without_dataset_config_changes_none_of_it(auth_client, dataset_a):
    dataset, question = dataset_a

    response = auth_client.put(
        _behavior_url(dataset.id),
        {"column_config": {str(question.id): {"is_visible": False}}},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    dataset.refresh_from_db()
    assert dataset.dataset_config == {
        "eval_recommendations": ["groundedness"],
        "environment_labels": ["prod"],
    }
    # Hiding a column keeps its role.
    assert dataset.column_config[str(question.id)] == {
        "is_visible": False,
        "is_frozen": None,
        "role": "input",
    }


@pytest.mark.django_db
def test_dataset_config_must_be_an_object(auth_client, dataset_a):
    dataset, _question = dataset_a

    response = auth_client.put(
        _behavior_url(dataset.id),
        {"dataset_config": ["dismiss_banner"]},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    dataset.refresh_from_db()
    assert "dismiss_banner" not in dataset.dataset_config


@pytest.mark.django_db
@pytest.mark.parametrize(
    "dataset_config",
    [
        {"schema": {"declared": {}}},
        {"dismiss_banner": True, "environment_labels": ["prod", "qa"]},
        {"eval_recommendations": []},
    ],
    ids=["schema", "with-dismiss-banner", "server-key"],
)
def test_a_setting_the_endpoint_does_not_save_is_refused(
    auth_client, dataset_a, dataset_config
):
    """Never a 200 that drops what was sent: the whole save is refused."""
    dataset, question = dataset_a
    before = (dataset.name, dict(dataset.dataset_config), dict(dataset.column_config))

    response = auth_client.put(
        _behavior_url(dataset.id),
        {
            "dataset_name": "l4-renamed",
            "column_config": {str(question.id): {"role": "metadata"}},
            "dataset_config": dataset_config,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    body = response.content.decode()
    for key in dataset_config:
        if key != "dismiss_banner":
            assert key in body
    dataset.refresh_from_db()
    assert (dataset.name, dataset.dataset_config, dataset.column_config) == before


@pytest.mark.django_db
def test_dismiss_banner_must_be_true_or_false(auth_client, dataset_a):
    dataset, _question = dataset_a

    response = auth_client.put(
        _behavior_url(dataset.id),
        {"dataset_config": {"dismiss_banner": "yes"}},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    dataset.refresh_from_db()
    assert "dismiss_banner" not in dataset.dataset_config


@pytest.mark.django_db
def test_other_org_cannot_change_the_dataset(auth_client, dataset_a, tenant_b):
    dataset, question = dataset_a
    _org_b, _ws_b, client_b = tenant_b
    before = (dataset.name, dict(dataset.dataset_config), dict(dataset.column_config))

    response = client_b.put(
        _behavior_url(dataset.id),
        {
            "dataset_name": "taken-over-by-b",
            "dataset_config": {"dismiss_banner": True},
            "column_config": {
                str(question.id): {"is_visible": False, "role": "metadata"}
            },
            "column_order": [str(question.id)],
        },
        format="json",
    )

    # Refused as not found (no hint the dataset exists), and nothing changed.
    assert response.status_code == status.HTTP_404_NOT_FOUND, response.content
    assert "taken-over-by-b" not in response.content.decode()
    dataset.refresh_from_db()
    assert (dataset.name, dataset.dataset_config, dataset.column_config) == before

    # Positive control: the owner's own save of the same body is accepted.
    own = auth_client.put(
        _behavior_url(dataset.id),
        {"dataset_config": {"dismiss_banner": True}},
        format="json",
    )
    assert own.status_code == status.HTTP_200_OK, own.content
    dataset.refresh_from_db()
    assert dataset.dataset_config["dismiss_banner"] is True


@pytest.mark.django_db
def test_other_org_cannot_name_a_column_of_another_dataset(
    auth_client, dataset_a, tenant_b
):
    dataset, question = dataset_a
    org_b, ws_b, client_b = tenant_b
    dataset_b = Dataset.no_workspace_objects.create(
        name="l4-b-own",
        organization=org_b,
        workspace=ws_b,
        source=DatasetSourceChoices.BUILD.value,
    )

    response = client_b.put(
        _behavior_url(dataset_b.id),
        {"column_config": {str(question.id): {"role": "expected"}}},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    dataset.refresh_from_db()
    dataset_b.refresh_from_db()
    assert dataset.column_config[str(question.id)]["role"] == "input"
    assert str(question.id) not in (dataset_b.column_config or {})
