"""Cell access must agree with the requested dataset, organization and workspace."""

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.workspace import Workspace
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.tests.test_dataset_list_tenancy import tenants as tenants_fixture
from model_hub.views.develop_dataset import GetCellDataView, UpdateCellValueView
from tfc.middleware.workspace_context import clear_workspace_context

tenants = tenants_fixture
pytestmark = pytest.mark.django_db


def _request(view, tenant, data, **kwargs):
    # Exercise request-based authorization without relying on a ContextVar that
    # may be absent in a worker or captured before a request exists.
    clear_workspace_context()
    request = APIRequestFactory().post("/", data, format="json")
    force_authenticate(request, user=tenant["user"])
    request.organization = tenant["org"]
    request.workspace = tenant["workspace"]
    return view.as_view()(request, **kwargs)


def _grid(tenant, workspace=None):
    dataset = Dataset.objects.create(
        name="another dataset", organization=tenant["org"], user=tenant["user"],
        workspace=workspace or tenant["workspace"], source=DatasetSourceChoices.BUILD.value,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    column = Column.objects.create(
        dataset=dataset, name="question", data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    cell = Cell.objects.create(dataset=dataset, row=row, column=column, value="unchanged")
    return dataset, row, column, cell


def _write(tenant, dataset, row, column):
    return _request(UpdateCellValueView, tenant, {
        "row_id": str(row.id), "column_id": str(column.id), "new_value": "changed",
    }, dataset_id=dataset.id)


def test_same_org_write_without_ambient_workspace(tenants):
    a, _, _ = tenants
    response = _write(a, a["dataset"], a["rows"][0], a["column"])
    assert response.status_code == 200, response.data
    a["cells"][0].refresh_from_db()
    assert a["cells"][0].value == "changed"


def test_same_org_legacy_cell_without_redundant_dataset_link(tenants):
    a, _, _ = tenants
    Cell.objects.filter(pk=a["cells"][0].pk).update(dataset=None)
    response = _write(a, a["dataset"], a["rows"][0], a["column"])
    assert response.status_code == 200, response.data
    a["cells"][0].refresh_from_db()
    assert a["cells"][0].value == "changed"


@pytest.mark.parametrize("url_owner", ["resource_owner", "caller"])
def test_cell_update_requires_matching_tenant_scope(tenants, url_owner):
    a, b, _ = tenants
    dataset = a["dataset"] if url_owner == "resource_owner" else b["dataset"]
    response = _write(b, dataset, a["rows"][0], a["column"])
    assert response.status_code == 404, response.data
    a["cells"][0].refresh_from_db()
    assert a["cells"][0].value == "a-value-0"


def test_same_org_wrong_dataset_never_falls_back(tenants):
    a, _, _ = tenants
    other, _, _, _ = _grid(a)
    response = _write(a, other, a["rows"][0], a["column"])
    assert response.status_code == 404, response.data
    a["cells"][0].refresh_from_db()
    assert a["cells"][0].value == "a-value-0"


def test_other_workspace_write_is_404(tenants):
    a, _, _ = tenants
    workspace = Workspace.objects.create(
        name="private", organization=a["org"], created_by=a["user"], is_default=False,
    )
    dataset, row, column, cell = _grid(a, workspace)
    response = _write(a, dataset, row, column)
    assert response.status_code == 404, response.data
    cell.refresh_from_db()
    assert cell.value == "unchanged"


@pytest.mark.parametrize("inconsistent_link", ["row", "column"])
def test_cell_relationships_must_match_dataset(tenants, inconsistent_link):
    a, b, _ = tenants
    cell = a["cells"][0]
    linked = b["rows"][0] if inconsistent_link == "row" else b["column"]
    Cell.objects.filter(pk=cell.pk).update(**{inconsistent_link: linked})
    cell.refresh_from_db()
    response = _write(a, a["dataset"], cell.row, cell.column)
    assert response.status_code == 404, response.data
    cell.refresh_from_db()
    assert cell.value == "a-value-0"


@pytest.mark.parametrize("deleted_object", ["dataset", "row", "column", "cell"])
def test_deleted_grid_components_cannot_be_written(tenants, deleted_object):
    a, _, _ = tenants
    component = {"dataset": a["dataset"], "row": a["rows"][0],
                 "column": a["column"], "cell": a["cells"][0]}[deleted_object]
    type(component).all_objects.filter(pk=component.pk).update(deleted=True)
    response = _write(a, a["dataset"], a["rows"][0], a["column"])
    assert response.status_code == 404, response.data
    assert Cell.all_objects.get(pk=a["cells"][0].pk).value == "a-value-0"


def test_cell_read_same_org_positive_control(tenants):
    a, _, _ = tenants
    response = _request(GetCellDataView, a, {
        "row_ids": [str(a["rows"][0].id)], "column_ids": [str(a["column"].id)],
    })
    assert response.status_code == 200, response.data
    assert response.data["result"][str(a["rows"][0].id)][str(a["column"].id)]["cell_value"] == "a-value-0"


@pytest.mark.parametrize("foreign_scope", ["organization", "workspace"])
def test_cell_read_rejects_foreign_grid(tenants, foreign_scope):
    a, b, _ = tenants
    if foreign_scope == "workspace":
        workspace = Workspace.objects.create(
            name="private read", organization=a["org"], created_by=a["user"], is_default=False,
        )
        _, row, column, cell = _grid(a, workspace)
    else:
        row, column, cell = b["rows"][0], b["column"], b["cells"][0]
    original = cell.value
    response = _request(GetCellDataView, a, {
        "row_ids": [str(row.id)], "column_ids": [str(column.id)],
    })
    assert response.status_code == 404, response.data
    cell.refresh_from_db()
    assert cell.value == original


@pytest.mark.parametrize("scope", ["own", "organization", "workspace"])
def test_column_config_scope(tenants, scope):
    from model_hub.models.run_prompt import RunPrompter
    from model_hub.views.develop_dataset import ColumnConfigView

    a, b, _ = tenants
    owner = b if scope == "organization" else a
    dataset, column = owner["dataset"], owner["column"]
    if scope == "workspace":
        workspace = Workspace.objects.create(
            name="private config", organization=a["org"], created_by=a["user"], is_default=False,
        )
        dataset, _, column, _ = _grid(a, workspace)
    prompt = RunPrompter.objects.create(
        dataset=dataset, organization=owner["org"], workspace=dataset.workspace,
        model="offline", name="config", messages=[],
    )
    Column.objects.filter(pk=column.pk).update(source=SourceChoices.RUN_PROMPT.value, source_id=str(prompt.pk))
    clear_workspace_context()
    request = APIRequestFactory().get("/")
    force_authenticate(request, user=a["user"])
    request.organization, request.workspace = a["org"], a["workspace"]
    response = ColumnConfigView.as_view()(request, column_id=column.pk)
    assert response.status_code == (200 if scope == "own" else 404), response.data
    if scope == "own":
        assert response.data["result"]["model"] == "offline"
    prompt.refresh_from_db()
    assert prompt.name == "config"
