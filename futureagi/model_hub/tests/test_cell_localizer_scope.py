"""Localizer reads and writes inherit cell ownership from its row's dataset."""

import json

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.workspace import Workspace
from model_hub.models.develop_dataset import Cell
from model_hub.models.error_localizer_model import ErrorLocalizerTask
from model_hub.tests.test_dataset_cell_scope import _grid
from model_hub.tests.test_dataset_list_tenancy import tenants as tenants_fixture
from model_hub.views.separate_evals import CellErrorLocalizerView
from tfc.middleware.workspace_context import clear_workspace_context

tenants = tenants_fixture
pytestmark = pytest.mark.django_db


def _call(caller, method, cell):
    clear_workspace_context()
    request = getattr(APIRequestFactory(), method)("/", {}, format="json")
    request.organization, request.workspace = caller["org"], caller["workspace"]
    force_authenticate(request, user=caller["user"])
    return CellErrorLocalizerView.as_view()(request, cell_id=cell.pk)


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("scope", ["organization", "workspace", "null_dataset"])
def test_localizer_foreign_cell_is_not_found_without_writes(tenants, method, scope):
    a, b, _ = tenants
    if scope == "workspace":
        workspace = Workspace.objects.create(
            name="private localizer", organization=a["org"], created_by=a["user"], is_default=False,
        )
        _, _, _, cell = _grid(a, workspace)
    else:
        cell = b["cells"][0]
    if scope == "null_dataset":
        Cell.objects.filter(pk=cell.pk).update(dataset=None)
    Cell.objects.filter(pk=cell.pk).update(value_infos=json.dumps({"error_analysis": {"private": "canary"}}))
    before_cells = list(Cell.all_objects.order_by("id").values())
    before_tasks = list(ErrorLocalizerTask.objects.order_by("id").values())
    response = _call(a, method, cell)
    assert response.status_code == 404, response.data
    assert "canary" not in str(response.data)
    assert list(Cell.all_objects.order_by("id").values()) == before_cells
    assert list(ErrorLocalizerTask.objects.order_by("id").values()) == before_tasks


def test_localizer_own_cell_metadata_is_readable(tenants):
    a, _, _ = tenants
    cell = a["cells"][0]
    Cell.objects.filter(pk=cell.pk).update(value_infos=json.dumps({"error_analysis": {"own": "analysis"}}))
    response = _call(a, "get", cell)
    assert response.status_code == 200, response.data
    assert response.data["result"]["error_analysis"] == {"own": "analysis"}


def test_localizer_own_non_evaluation_cell_keeps_validation(tenants):
    a, _, _ = tenants
    response = _call(a, "post", a["cells"][0])
    assert response.status_code == 400, response.data
    assert "only available for evaluation cells" in str(response.data)
