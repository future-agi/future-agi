"""Dataset details persist through the public, workspace-scoped behavior API."""

import uuid

import pytest

from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.choices import DatasetSourceChoices
from model_hub.models.develop_dataset import Dataset
from model_hub.tests.test_dsx_l4_dataset_behavior import _second_tenant
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles


@pytest.fixture
def tenant_b(db):
    org, workspace, client = _second_tenant("Dataset settings tenant")
    yield org, workspace, client
    client.stop_workspace_injection()


LIST_URL = "/model-hub/develops/get-datasets/"


def settings_url(dataset):
    return f"/model-hub/develops/{dataset.id}/edit_dataset_behavior/"


@pytest.fixture
def dataset(organization, workspace):
    return Dataset.no_workspace_objects.create(
        name=f"settings-{uuid.uuid4().hex[:10]}",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
        dataset_config={"eval_recommendations": ["groundedness"]},
    )


def listed(client, dataset):
    response = client.get(LIST_URL, {"search_text": dataset.name})
    assert response.status_code == 200, response.content
    return response.data["result"]["datasets"]


@pytest.mark.django_db
def test_existing_dataset_has_empty_details(auth_client, dataset):
    row = listed(auth_client, dataset)[0]
    assert row["description"] == ""
    assert row["tags"] == []


@pytest.mark.django_db
def test_save_read_and_clear_details(auth_client, dataset):
    details = {
        "description": "Support regression cases",
        "tags": ["golden", "support.v2"],
    }
    saved = auth_client.put(settings_url(dataset), details, format="json")
    assert saved.status_code == 200, saved.content
    dataset.refresh_from_db()
    assert dataset.description == details["description"]
    assert dataset.tags == details["tags"]
    assert dataset.dataset_config == {"eval_recommendations": ["groundedness"]}
    row = listed(auth_client, dataset)[0]
    assert {key: row[key] for key in details} == details

    # A detail read does not depend on the dataset appearing on the first page.
    response = auth_client.get(settings_url(dataset))
    assert response.status_code == 200, response.content
    assert response.data["result"] == {
        "id": str(dataset.id),
        "name": dataset.name,
        **details,
    }

    renamed = auth_client.put(
        settings_url(dataset),
        {"dataset_name": dataset.name + "-renamed"},
        format="json",
    )
    assert renamed.status_code == 200, renamed.content
    dataset.refresh_from_db()
    assert dataset.description == details["description"]
    assert dataset.tags == details["tags"]

    cleared = auth_client.put(
        settings_url(dataset), {"description": "", "tags": []}, format="json"
    )
    assert cleared.status_code == 200, cleared.content
    dataset.refresh_from_db()
    assert dataset.description == ""
    assert dataset.tags == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "invalid",
    [
        {"description": "a" * 501},
        {"description": None},
        {"description": 7},
        {"tags": "golden"},
        {"tags": None},
        {"tags": [7]},
        {"tags": ["Invalid Tag"]},
        {"tags": ["a" * 31]},
        {"tags": ["golden", "golden"]},
        {"tags": [f"tag-{n}" for n in range(21)]},
    ],
)
def test_invalid_details_are_rejected_without_partial_rename(
    auth_client, dataset, invalid
):
    before_name = dataset.name
    response = auth_client.put(
        settings_url(dataset),
        {"dataset_name": "must-not-save", **invalid},
        format="json",
    )
    assert response.status_code == 400, response.content
    dataset.refresh_from_db()
    assert dataset.name == before_name
    assert dataset.dataset_config == {"eval_recommendations": ["groundedness"]}


@pytest.mark.django_db
def test_other_organization_cannot_read_or_write_details(
    auth_client, dataset, tenant_b
):
    _org_b, _workspace_b, other = tenant_b
    own = auth_client.put(
        settings_url(dataset),
        {"description": "Private description", "tags": ["private"]},
        format="json",
    )
    assert own.status_code == 200, own.content
    assert listed(other, dataset) == []
    read = other.get(settings_url(dataset))
    assert read.status_code == 404, read.content
    assert "Private description" not in read.content.decode()
    write = other.put(
        settings_url(dataset),
        {"description": "Foreign write", "tags": []},
        format="json",
    )
    assert write.status_code == 404, write.content
    dataset.refresh_from_db()
    assert dataset.description == "Private description"
    assert dataset.tags == ["private"]


@pytest.mark.django_db
def test_sibling_workspace_cannot_read_or_write_details(
    auth_client, dataset, user, organization
):
    from conftest import WorkspaceAwareAPIClient

    sibling = Workspace.no_workspace_objects.create(
        name=f"sibling-{uuid.uuid4().hex[:10]}",
        organization=organization,
        created_by=user,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=sibling,
        role=OrganizationRoles.WORKSPACE_ADMIN,
        level=Level.WORKSPACE_ADMIN,
        is_active=True,
    )
    other = WorkspaceAwareAPIClient()
    other.force_authenticate(user=user)
    other.set_workspace(sibling)
    try:
        assert listed(other, dataset) == []
        read = other.get(settings_url(dataset))
        assert read.status_code == 404, read.content
        write = other.put(
            settings_url(dataset),
            {"description": "Wrong workspace", "tags": []},
            format="json",
        )
        assert write.status_code == 404, write.content
    finally:
        other.stop_workspace_injection()


@pytest.mark.django_db
def test_deleted_dataset_details_are_not_readable(auth_client, dataset):
    Dataset.no_workspace_objects.filter(id=dataset.id).update(deleted=True)
    assert listed(auth_client, dataset) == []
    response = auth_client.get(settings_url(dataset))
    assert response.status_code == 404, response.content
