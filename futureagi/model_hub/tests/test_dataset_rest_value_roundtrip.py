"""REST-001/002: preserve JSON values through authenticated writes and reads."""

import json
import uuid
from unittest.mock import Mock

import pytest

from model_hub.models.choices import SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.tests.test_dataset_schema import _client
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import _tenant
from model_hub.views import develop_dataset as views
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def rest_dataset(db, monkeypatch, urlconf_loaded_without_workspace_context):
    # Keep real authentication, serializers, ORM writes and table reads. Only
    # external metering is disabled; schema validation is never mocked.
    monkeypatch.setattr(views, "log_and_deduct_cost_for_resource_request", None)
    tenant = _tenant("rest-values")
    try:
        yield tenant, _client(tenant)
    finally:
        clear_workspace_context()


def _declare(tenant, properties, required=True):
    dataset = tenant["dataset"]
    dataset.dataset_config = {
        "schema": {
            "enforce": "reject",
            "declared": {
                "type": "object",
                "properties": properties,
                "required": list(properties) if required else [],
                "additionalProperties": False,
            },
        }
    }
    dataset.save(update_fields=["dataset_config"])


def _table_row(client, tenant, row_id):
    response = client.get(
        f"/model-hub/develops/{tenant['dataset'].id}/get-dataset-table/"
    )
    assert response.status_code == 200, response.content
    return next(
        row
        for row in response.json()["result"]["table"]
        if row["row_id"] == str(row_id)
    )


@pytest.mark.parametrize("enforce", [False, True], ids=["no-schema", "reject-schema"])
@pytest.mark.parametrize(
    "data_type,value,stored",
    [
        pytest.param("integer", 0, "0", id="native-zero"),
        pytest.param("float", 0.0, "0.0", id="native-float-zero"),
        pytest.param("boolean", False, "false", id="native-false"),
        pytest.param("boolean", True, "true", id="native-true"),
        pytest.param("integer", "0", "0", id="string-zero-control"),
        pytest.param("float", "0.0", "0.0", id="string-float-zero-control"),
        pytest.param("boolean", "false", "false", id="string-false-control"),
        pytest.param("boolean", "true", "true", id="string-true-control"),
        pytest.param("integer", 7, "7", id="nonzero-integer-control"),
        pytest.param("float", 1.5, "1.5", id="nonzero-float-control"),
    ],
)
def test_cell_edit_preserves_falsy_values_and_controls(
    rest_dataset, enforce, data_type, value, stored
):
    tenant, client = rest_dataset
    column = tenant["column"]
    column.data_type = data_type
    column.save(update_fields=["data_type"])
    if enforce:
        schema_type = "number" if data_type == "float" else data_type
        _declare(tenant, {column.name: {"type": schema_type}})

    cell = tenant["cells"][0]
    response = client.post(
        f"/model-hub/develops/{tenant['dataset'].id}/update_cell_value/",
        {
            "row_id": str(cell.row_id),
            "column_id": str(column.id),
            "new_value": value,
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    cell.refresh_from_db()
    assert cell.value == stored
    row = _table_row(client, tenant, cell.row_id)
    assert row[str(column.id)]["cell_value"] == stored


@pytest.mark.parametrize("enforce", [False, True], ids=["no-schema", "reject-schema"])
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="absent"),
        pytest.param({"new_value": None}, id="null"),
        pytest.param({"new_value": ""}, id="empty-string"),
        pytest.param({"new_value": " \t\n"}, id="blank-string"),
    ],
)
def test_cell_edit_still_clears_absent_null_and_blank_values(
    rest_dataset, enforce, payload
):
    tenant, client = rest_dataset
    column = tenant["column"]
    column.data_type = "integer"
    column.save(update_fields=["data_type"])
    if enforce:
        _declare(tenant, {column.name: {"type": ["integer", "null"]}}, required=False)
    cell = tenant["cells"][0]
    response = client.post(
        f"/model-hub/develops/{tenant['dataset'].id}/update_cell_value/",
        {"row_id": str(cell.row_id), "column_id": str(column.id), **payload},
        format="json",
    )
    assert response.status_code == 200, response.content
    cell.refresh_from_db()
    assert cell.value is None
    assert _table_row(client, tenant, cell.row_id)[str(column.id)]["cell_value"] is None


def _json_columns(tenant, enforce):
    payload = tenant["column"]
    payload.name = "payload"
    payload.data_type = "json"
    payload.save(update_fields=["name", "data_type"])
    tags = Column.no_workspace_objects.create(
        dataset=tenant["dataset"],
        name="tags",
        data_type="array",
        source=SourceChoices.OTHERS.value,
    )
    if enforce:
        _declare(
            tenant,
            {
                "payload": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                },
                "tags": {"type": "array"},
            },
        )
    return payload, tags


def _batch_row(row_id, payload, tags):
    return {
        "id": str(row_id),
        "cells": [
            {"column_name": "payload", "value": payload},
            {"column_name": "tags", "value": tags},
        ],
    }


@pytest.mark.parametrize("enforce", [False, True], ids=["no-schema", "reject-schema"])
@pytest.mark.parametrize("encoded", [False, True], ids=["native", "string-control"])
@pytest.mark.parametrize(
    "payload,tags",
    [
        pytest.param({"ok": True}, ["a", "b"], id="reported-repro"),
        pytest.param(
            {"ok": False, "nested": {"name": "l'été", "nothing": None}},
            ["quoted \"tag\"", False, 0, None, {"nested": [True]}],
            id="nested-json",
        ),
        pytest.param({}, [], id="empty-containers"),
    ],
)
def test_added_json_and_array_cells_round_trip(
    rest_dataset, enforce, encoded, payload, tags
):
    tenant, client = rest_dataset
    columns = _json_columns(tenant, enforce)
    original = [payload, tags]
    values = (
        [json.dumps(value, ensure_ascii=False, indent=2) for value in original]
        if encoded
        else original
    )
    row_id = uuid.uuid4()
    response = client.post(
        f"/model-hub/develops/{tenant['dataset'].id}/add_rows/",
        {"rows": [_batch_row(row_id, *values)]},
        format="json",
    )
    assert response.status_code == 200, response.content
    table_row = _table_row(client, tenant, row_id)
    for column, expected, submitted in zip(columns, original, values):
        cell = Cell.no_workspace_objects.get(
            dataset=tenant["dataset"], row_id=row_id, column=column
        )
        assert isinstance(cell.value, str)
        assert json.loads(cell.value) == expected
        if encoded:
            assert cell.value == submitted  # Do not double-encode or reformat strings.
        returned = table_row[str(column.id)]["cell_value"]
        assert returned == cell.value
        assert json.loads(returned) == expected


def test_rejecting_schema_still_refuses_invalid_native_json_without_partial_batch(
    rest_dataset,
):
    tenant, client = rest_dataset
    _json_columns(tenant, enforce=True)
    row_ids = [uuid.uuid4(), uuid.uuid4()]
    response = client.post(
        f"/model-hub/develops/{tenant['dataset'].id}/add_rows/",
        {
            "rows": [
                _batch_row(row_ids[0], {"ok": True}, ["a"]),
                _batch_row(row_ids[1], {"ok": "not-a-boolean"}, ["b"]),
            ]
        },
        format="json",
    )
    assert response.status_code == 400, response.content
    assert not Row.no_workspace_objects.filter(id__in=row_ids).exists()
    assert not Cell.no_workspace_objects.filter(row_id__in=row_ids).exists()


@pytest.mark.parametrize("enforce", [False, True], ids=["no-schema", "reject-schema"])
def test_added_json_rejects_nonfinite_numbers_before_billing_or_rows(
    rest_dataset, enforce, monkeypatch
):
    tenant, client = rest_dataset
    _json_columns(tenant, enforce)
    billing = Mock(side_effect=AssertionError("Invalid JSON must not reach billing"))
    monkeypatch.setattr(views, "log_and_deduct_cost_for_resource_request", billing)
    row_ids = [uuid.uuid4(), uuid.uuid4()]
    body = json.dumps(
        {
            "rows": [
                _batch_row(row_ids[0], {"ok": True}, ["a"]),
                _batch_row(row_ids[1], {"overflow": "EXPONENT"}, ["b"]),
            ]
        }
    ).replace('"EXPONENT"', "1e400")
    response = client.post(
        f"/model-hub/develops/{tenant['dataset'].id}/add_rows/",
        body,
        content_type="application/json",
    )
    assert response.status_code == 400, response.content
    billing.assert_not_called()
    assert not Row.no_workspace_objects.filter(id__in=row_ids).exists()
    assert not Cell.no_workspace_objects.filter(row_id__in=row_ids).exists()
