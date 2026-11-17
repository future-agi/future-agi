"""ORGS-02: row-detail POST is a scoped read, never a write exemption elsewhere."""

import pytest
from rest_framework.test import APIClient

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles


@pytest.fixture
def viewer_dataset(user, workspace, monkeypatch):
    OrganizationMembership.no_workspace_objects.filter(user=user).update(
        role=OrganizationRoles.MEMBER_VIEW_ONLY, level=Level.VIEWER
    )
    WorkspaceMembership.no_workspace_objects.filter(user=user).update(
        role=OrganizationRoles.WORKSPACE_VIEWER, level=Level.WORKSPACE_VIEWER
    )
    user.organization_role = OrganizationRoles.MEMBER_VIEW_ONLY
    user.save(update_fields=["organization_role"])
    user = type(user).objects.get(pk=user.pk)
    assert not user.can_write_to_workspace(workspace)
    monkeypatch.setattr(
        "accounts.authentication.decode_token", lambda _: (user, "fixture-token", None)
    )
    dataset = Dataset.no_workspace_objects.create(
        name="Viewer dataset", organization=user.organization, workspace=workspace
    )
    column = Column.no_workspace_objects.create(
        name="question",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
        dataset=dataset,
    )
    row = Row.no_workspace_objects.create(dataset=dataset, order=1)
    cell = Cell.no_workspace_objects.create(
        dataset=dataset, row=row, column=column, value="readable fixture"
    )
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION="Bearer offline-fixture",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
        HTTP_X_ORGANIZATION_ID=str(user.organization_id),
    )
    return client, dataset, row, column, cell


@pytest.mark.django_db
def test_viewer_can_read_grid_and_row_without_write_access(viewer_dataset):
    client, dataset, row, column, cell = viewer_dataset
    base = f"/model-hub/develops/{dataset.id}"
    # The current-row export uses this same authorized table read and page shape.
    grid = client.get(
        f"{base}/get-dataset-table/",
        {"page_size": 500, "current_page_index": 0, "filters": "[]"},
    )
    assert grid.status_code == 200, grid.data
    assert "readable fixture" in str(grid.data)
    detail = client.post(
        f"{base}/get-row-data/", {"row_id": str(row.id)}, format="json"
    )
    assert detail.status_code == 200, detail.data
    assert detail.data["result"]["current"]["row_id"] == str(row.id)
    mutation = client.post(
        f"{base}/update_cell_value/",
        {"row_id": str(row.id), "column_id": str(column.id), "new_value": "forbidden"},
        format="json",
    )
    assert mutation.status_code == 403
    cell.refresh_from_db()
    assert cell.value == "readable fixture"


@pytest.mark.django_db
def test_row_read_never_reveals_another_organizations_dataset(viewer_dataset):
    client, _, row, _, _ = viewer_dataset
    other = Organization.objects.create(name="Other fixture organization")
    foreign = Dataset.no_workspace_objects.create(
        name="Private fixture", organization=other
    )
    response = client.post(
        f"/model-hub/develops/{foreign.id}/get-row-data/",
        {"row_id": str(row.id)},
        format="json",
    )
    assert response.status_code == 404
    assert "Private fixture" not in str(response.data)
    assert "readable fixture" not in str(response.data)
