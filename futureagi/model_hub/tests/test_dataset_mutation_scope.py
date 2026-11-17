"""Dataset endpoints must scope identifiers before touching the grid."""

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.workspace import Workspace
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.tests.test_dataset_cell_scope import _grid
from model_hub.tests.test_dataset_list_tenancy import tenants as tenants_fixture
from model_hub.views import develop_dataset as views
from tfc.middleware.workspace_context import clear_workspace_context

tenants = tenants_fixture
pytestmark = pytest.mark.django_db

ENDPOINTS = [
    ("AddEmptyColumnsView", "post"),
    ("AddEmptyRowsView", "post"),
    ("AddDataRowsView", "post"),
    ("DeleteColumnView", "delete"),
    ("DeleteRowView", "delete"),
    ("EditDatasetBehaviorView", "put"),
    ("UpdateRowView", "put"),
    ("UpdateColumnTypeView", "put"),
    ("DownloadDatasetView", "get"),
    ("DownloadComparisonDatasetView", "post"),
]


def _payload(name, dataset, row, column):
    return {
        "AddEmptyColumnsView": {"num_cols": 1},
        "AddEmptyRowsView": {"num_rows": 1},
        "AddDataRowsView": {"rows": [{"cells": [{"column_name": "question", "value": "added"}]}]},
        "DeleteColumnView": {},
        "DeleteRowView": {"row_ids": [str(row.id)]},
        "EditDatasetBehaviorView": {"dataset_name": "renamed"},
        "UpdateRowView": {"rows": [{"id": str(row.id), "cells": [{"column_name": "question", "value": "changed"}]}]},
        "UpdateColumnTypeView": {"new_column_type": column.data_type, "preview": True},
        "DownloadDatasetView": {},
        "DownloadComparisonDatasetView": {
            "base_column_name": "question", "dataset_ids": [str(dataset.id)],
            "common_column_names": ["question"],
            "dataset_info": {"key": {str(dataset.id): str(row.id)}},
        },
    }[name]


def _call(name, method, caller, dataset, row, column, payload=None):
    clear_workspace_context()
    request = getattr(APIRequestFactory(), method)(
        "/", _payload(name, dataset, row, column) if payload is None else payload,
        format="json",
    )
    force_authenticate(request, user=caller["user"])
    request.organization = caller["org"]
    request.workspace = caller["workspace"]
    kwargs = {"dataset_id": dataset.id}
    if name in {"DeleteColumnView", "UpdateColumnTypeView"}:
        kwargs["column_id"] = column.id
    return getattr(views, name).as_view()(request, **kwargs)


def _snapshot():
    clear_workspace_context()
    return {
        model.__name__: list(model.all_objects.order_by("id").values())
        for model in (Dataset, Column, Row, Cell)
    }


@pytest.mark.parametrize("name,method", ENDPOINTS)
@pytest.mark.parametrize("foreign_scope", ["organization", "workspace"])
def test_foreign_grid_is_404_without_any_write(tenants, monkeypatch, name, method, foreign_scope):
    a, b, _ = tenants
    monkeypatch.setattr(views, "log_and_deduct_cost_for_resource_request", None)
    if foreign_scope == "workspace":
        workspace = Workspace.objects.create(
            name="private mutation", organization=a["org"], created_by=a["user"], is_default=False,
        )
        dataset, row, column, _ = _grid(a, workspace)
    else:
        dataset, row, column = b["dataset"], b["rows"][0], b["column"]
    before = _snapshot()
    response = _call(name, method, a, dataset, row, column)
    assert response.status_code == 404, getattr(response, "data", response.status_code)
    assert _snapshot() == before


@pytest.mark.parametrize("name,method", ENDPOINTS)
def test_same_org_grid_still_works(tenants, monkeypatch, name, method):
    a, _, _ = tenants
    monkeypatch.setattr(views, "log_and_deduct_cost_for_resource_request", None)
    response = _call(name, method, a, a["dataset"], a["rows"][0], a["column"])
    assert response.status_code == 200, getattr(response, "data", response.status_code)
    if getattr(response, "streaming", False):
        assert b"a-value-0" in b"".join(response.streaming_content)
    if name == "AddEmptyColumnsView":
        assert Column.objects.filter(dataset=a["dataset"]).count() == 2
    elif name in {"AddEmptyRowsView", "AddDataRowsView"}:
        assert Row.objects.filter(dataset=a["dataset"]).count() == 3
    elif name == "DeleteColumnView":
        assert not Column.objects.filter(pk=a["column"].pk).exists()
    elif name == "DeleteRowView":
        assert not Row.objects.filter(pk=a["rows"][0].pk).exists()
    elif name == "EditDatasetBehaviorView":
        assert Dataset.objects.get(pk=a["dataset"].pk).name == "renamed"
    elif name == "UpdateRowView":
        assert Cell.objects.get(pk=a["cells"][0].pk).value == "changed"


def test_comparison_export_rejects_foreign_row_under_own_dataset(tenants):
    a, b, _ = tenants
    data = _payload("DownloadComparisonDatasetView", a["dataset"], a["rows"][0], a["column"])
    data["dataset_info"] = {"key": {str(a["dataset"].id): str(b["rows"][0].id)}}
    before = _snapshot()
    response = _call("DownloadComparisonDatasetView", "post", a, a["dataset"], a["rows"][0], a["column"], data)
    assert response.status_code == 404
    assert _snapshot() == before
