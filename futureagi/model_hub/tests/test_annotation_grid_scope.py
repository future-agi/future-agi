"""Annotation writes validate every grid link before making any change."""

import io

import pytest
from django.http import Http404
from rest_framework.exceptions import NotFound

from accounts.models.workspace import Workspace
from model_hub.models.choices import DataTypeChoices
from model_hub.models.develop_annotations import Annotations, AnnotationsLabels
from model_hub.models.develop_dataset import Cell
from model_hub.serializers.develop_annotations import AnnotationsSerializer
from model_hub.tests.test_dataset_cell_scope import _grid
from model_hub.tests.test_dataset_list_tenancy import tenants as tenants_fixture
from model_hub.tests.test_model_hub_related_scope import _request
from model_hub.views.develop_annotations import AnnotationsViewSet
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db
tenants = tenants_fixture


def _annotation(owner, dataset=True):
    clear_workspace_context()
    annotation = Annotations.no_workspace_objects.create(
        name="annotation", organization=owner["org"], workspace=owner["workspace"],
        dataset=owner["dataset"] if dataset else None,
    )
    annotation.assigned_users.add(owner["user"])
    annotation.columns.add(owner["column"])
    label = AnnotationsLabels.no_workspace_objects.create(
        name="label", organization=owner["org"], workspace=owner["workspace"],
        type="numeric", settings={},
    )
    annotation.labels.add(label)
    return annotation, label


def _snapshot():
    return list(Cell.all_objects.order_by("id").values())


@pytest.mark.parametrize("kind", ["label_values", "response_field_values"])
@pytest.mark.parametrize("foreign_link", ["row", "column", "both", "null_dataset"])
@pytest.mark.parametrize("foreign_scope", ["organization", "workspace"])
def test_annotation_update_rejects_foreign_grid_before_any_write(tenants, kind, foreign_link, foreign_scope):
    a, b, clients = tenants
    if foreign_scope == "workspace":
        workspace = Workspace.objects.create(name="private annotations", organization=b["org"], created_by=b["user"], is_default=False)
        dataset, row, column, cell = _grid(b, workspace)
        a = {"dataset": dataset, "rows": [row], "column": column, "cells": [cell]}
    annotation, label = _annotation(b, dataset=foreign_link != "null_dataset")
    if foreign_link == "null_dataset":
        Cell.objects.filter(pk=a["cells"][0].pk).update(dataset=None)
    row = b["rows"][0] if foreign_link == "column" else a["rows"][0]
    column = b["column"] if foreign_link == "row" else a["column"]
    own_update = {"row_id": str(b["rows"][0].id), "column_id": str(b["column"].id), "value": 3}
    foreign_update = {"row_id": str(row.id), "column_id": str(column.id), "value": 5}
    if kind == "label_values":
        own_update["label_id"] = foreign_update["label_id"] = str(label.id)
    before = _snapshot()
    response = clients["b"].post(
        f"/model-hub/annotations/{annotation.id}/update_cells/",
        {kind: [own_update, foreign_update]}, format="json",
    )
    assert response.status_code == 404, response.data
    assert _snapshot() == before


@pytest.mark.parametrize("kind", ["label_values", "response_field_values"])
def test_annotation_update_own_grid_control(tenants, kind):
    a, _, clients = tenants
    annotation, label = _annotation(a)
    update = {"row_id": str(a["rows"][0].id), "column_id": str(a["column"].id), "value": 5}
    if kind == "label_values":
        update["label_id"] = str(label.id)
    response = clients["a"].post(
        f"/model-hub/annotations/{annotation.id}/update_cells/", {kind: [update]}, format="json",
    )
    assert response.status_code == 200, response.data
    a["cells"][0].refresh_from_db()
    assert a["cells"][0].value == "5"


@pytest.mark.parametrize("own", [False, True])
def test_annotation_reset_row_scope(tenants, own):
    a, b, clients = tenants
    annotation, _ = _annotation(b)
    row = b["rows"][0] if own else a["rows"][0]
    before = _snapshot()
    response = clients["b"].post(
        f"/model-hub/annotations/{annotation.id}/reset_annotations/",
        {"row_id": str(row.id)}, format="json",
    )
    assert response.status_code == (200 if own else 404), response.data
    assert _snapshot() == before


def test_can_user_annotate_does_not_resolve_foreign_row(tenants):
    a, b, _ = tenants
    annotation, label = _annotation(b)
    view = AnnotationsViewSet()
    view.request = _request(b, "get", {})
    with pytest.raises((NotFound, Http404)):
        view.can_user_annotate(annotation.id, a["rows"][0].id, label.id, b["user"].id)


@pytest.mark.parametrize("field", ["columns", "dataset"])
@pytest.mark.parametrize("through_api", [False, True])
def test_annotation_relationships_reject_foreign_ids(tenants, field, through_api):
    a, b, clients = tenants
    annotation, _ = _annotation(b)
    payload = {field: [str(a["column"].id)] if field == "columns" else str(a["dataset"].id)}
    original_dataset = annotation.dataset_id
    original_columns = list(annotation.columns.values_list("id", flat=True))
    if through_api:
        response = clients["b"].patch(f"/model-hub/annotations/{annotation.id}/", payload, format="json")
        assert response.status_code == 404, response.data
    else:
        serializer = AnnotationsSerializer(annotation, data=payload, partial=True, context={"request": _request(b, "patch", {})})
        with pytest.raises(NotFound):
            serializer.is_valid(raise_exception=True)
    annotation.refresh_from_db()
    assert annotation.dataset_id == original_dataset
    assert list(annotation.columns.values_list("id", flat=True)) == original_columns


def test_annotation_dataset_null_rejected_without_changing_links(tenants):
    _, b, clients = tenants
    annotation, _ = _annotation(b)
    response = clients["b"].patch(f"/model-hub/annotations/{annotation.id}/", {"dataset": None}, format="json")
    assert response.status_code == 400, response.data
    annotation.refresh_from_db()
    assert annotation.dataset_id == b["dataset"].id


def test_annotation_relationships_own_control(tenants):
    a, _, clients = tenants
    annotation, _ = _annotation(a)
    response = clients["a"].patch(
        f"/model-hub/annotations/{annotation.id}/",
        {"dataset": str(a["dataset"].id), "columns": [str(a["column"].id)]}, format="json",
    )
    assert response.status_code == 200, response.data


@pytest.mark.parametrize("url_owner", ["resource_owner", "caller"])
def test_media_write_foreign_grid_is_404_and_unchanged(tenants, url_owner):
    a, b, clients = tenants
    column = a["column"]
    column.data_type = DataTypeChoices.IMAGE.value
    column.save()
    upload = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    upload.name = "image.png"
    before = _snapshot()
    dataset = a["dataset"] if url_owner == "resource_owner" else b["dataset"]
    response = clients["b"].post(
        f"/model-hub/develops/{dataset.id}/update_cell_value/",
        {"row_id": str(a["rows"][0].id), "column_id": str(column.id), "new_value": upload}, format="multipart",
    )
    assert response.status_code == 404, response.data
    assert _snapshot() == before
