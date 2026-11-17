"""The released SDK's row payload remains compatible with strict row writes."""

import ast
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

import pytest

from model_hub.constants import PYTHON_ADD_ROWS
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.serializers.contracts import DatasetAddRowsRequestSerializer

pytestmark = pytest.mark.django_db


# Exact Row.to_dict / Cell.to_dict shapes from the released SDK 0.7.0.
# UUIDs are generated even when callers only supply column_name and value.
# Row.to_dict sends exactly id, order, cells (no row_id or metadata).
def sdk_cell(column_name, value):
    return {
        "column_id": str(uuid4()),
        "row_id": str(uuid4()),
        "column_name": column_name,
        "value": value,
        "value_infos": [],
        "metadata": {},
        "status": None,
        "failure_reason": None,
    }


def sdk_row(cells, order=0):
    return {"id": str(uuid4()), "order": order, "cells": cells}


@pytest.fixture
def dataset(user, workspace):
    dataset = Dataset.objects.create(
        name="Legacy SDK rows",
        organization=user.organization,
        workspace=workspace,
        user=user,
    )
    Column.objects.create(
        dataset=dataset,
        name="input",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    return dataset


@pytest.fixture
def post_rows(auth_client, dataset):
    def post(payload):
        with patch(
            "model_hub.views.develop_dataset.log_and_deduct_cost_for_resource_request",
            None,
        ):
            return auth_client.post(
                f"/model-hub/develops/{dataset.id}/add_rows/",
                payload,
                format="json",
            )

    return post


def test_released_sdk_payload_creates_rows_and_values(post_rows, dataset):
    rows = [
        sdk_row([sdk_cell("input", "first value")], order=99),
        sdk_row([sdk_cell("input", "second value")], order=1),
    ]
    response = post_rows({"rows": rows})
    assert response.status_code == 200, response.data
    assert Row.objects.filter(dataset=dataset).count() == 2
    # Existing row-level id support is retained; order is still append order.
    for index, submitted in enumerate(rows):
        row = Row.objects.get(dataset=dataset, id=submitted["id"])
        assert row.order == index
        cell = Cell.objects.get(dataset=dataset, row=row, column__name="input")
        assert cell.value == submitted["cells"][0]["value"]
        assert str(cell.column_id) != submitted["cells"][0]["column_id"]
        assert str(cell.row_id) != submitted["cells"][0]["row_id"]


def test_generated_python_add_rows_payload(post_rows, dataset):
    # Evaluate only the rows assignment, never credentials, imports or network
    # calls. These factories reproduce the released SDK's to_dict output.
    assignment = next(
        node
        for node in ast.parse(PYTHON_ADD_ROWS).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "rows"
            for target in node.targets
        )
    )
    namespace = {"Row": sdk_row, "Cell": sdk_cell}
    exec(
        compile(ast.Module(body=[assignment], type_ignores=[]), "<rows>", "exec"),
        namespace,
    )
    rows = namespace["rows"]
    for name, data_type in [
        ("user_query", DataTypeChoices.TEXT.value),
        ("response_quality", DataTypeChoices.INTEGER.value),
        ("is_helpful", DataTypeChoices.BOOLEAN.value),
    ]:
        Column.objects.create(
            dataset=dataset,
            name=name,
            data_type=data_type,
            source=SourceChoices.OTHERS.value,
        )
    response = post_rows({"rows": rows})
    assert response.status_code == 200, response.data
    assert Row.objects.filter(dataset=dataset).count() == len(rows) == 2
    for submitted in rows:
        for expected in submitted["cells"]:
            saved = Cell.objects.get(
                dataset=dataset,
                row_id=submitted["id"],
                column__name=expected["column_name"],
            )
            assert saved.value == str(expected["value"])


@pytest.mark.parametrize("existing_target", ["same_dataset", "other_dataset"])
def test_client_cell_ids_and_metadata_never_address_or_update_objects(
    post_rows, dataset, user, workspace, existing_target
):
    owner = dataset
    if existing_target == "other_dataset":
        owner = Dataset.objects.create(
            name="Untouched dataset",
            organization=user.organization,
            workspace=workspace,
            user=user,
        )
        Column.objects.create(
            dataset=owner,
            name="input",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
    target_column = Column.objects.get(dataset=owner, name="input")
    target_row = Row.objects.create(dataset=owner, order=0)
    target_cell = Cell.objects.create(
        dataset=owner, column=target_column, row=target_row, value="unchanged"
    )
    submitted = sdk_row([sdk_cell("input", "new value")])
    submitted["cells"][0].update(
        column_id=str(target_column.id),
        row_id=str(target_row.id),
        value_infos=[{"ignored": "client value"}],
        metadata={"ignored": "client value"},
        status="ignored client status",
        failure_reason="ignored client failure",
    )
    response = post_rows({"rows": [submitted]})
    assert response.status_code == 200, response.data
    target_cell.refresh_from_db()
    assert target_cell.value == "unchanged"
    saved = Cell.objects.get(
        dataset=dataset, row_id=submitted["id"], column__name="input"
    )
    assert saved.row_id != target_row.id
    assert saved.column.dataset_id == dataset.id
    assert saved.value == "new value"
    assert saved.value_infos is None
    assert saved.column_metadata == {}
    assert saved.status == Cell._meta.get_field("status").get_default()
    assert Row.objects.filter(dataset=owner).count() == (2 if owner == dataset else 1)
    assert Row.objects.filter(dataset=dataset).count() == (2 if owner == dataset else 1)


@pytest.mark.parametrize(
    "key",
    ["column_id", "row_id", "value_infos", "metadata", "status", "failure_reason"],
)
def test_legacy_cell_values_are_discarded_before_validation(key):
    payload = {
        "rows": [
            {"cells": [{"column_name": "input", "value": "kept", key: {"any": [None]}}]}
        ]
    }
    original = deepcopy(payload)
    serializer = DatasetAddRowsRequestSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {
        "rows": [{"cells": [{"column_name": "input", "value": "kept"}]}]
    }
    assert payload == original


@pytest.mark.parametrize(
    "level,key",
    [
        ("cell", "foo"),
        ("cell", "colum_name"),
        ("cell", "values"),
        ("row", "foo"),
        ("row", "row_id"),
        ("row", "metadata"),
        ("request", "foo"),
    ],
)
def test_other_unknown_keys_still_refused_without_partial_writes(
    post_rows, dataset, level, key
):
    rows = [
        sdk_row([sdk_cell("input", "valid")]),
        sdk_row([sdk_cell("input", "invalid")]),
    ]
    payload = {"rows": rows}
    target = {"cell": rows[1]["cells"][0], "row": rows[1], "request": payload}[level]
    target[key] = "must not be ignored"
    response = post_rows(payload)
    assert response.status_code == 400, response.data
    assert "Unknown field." in str(response.data)
    assert key in str(response.data)
    assert not Row.objects.filter(dataset=dataset).exists()
    assert not Cell.objects.filter(dataset=dataset).exists()


@pytest.mark.parametrize("invalid", [None, "not an object", [], 7])
def test_non_object_cells_remain_invalid(post_rows, dataset, invalid):
    response = post_rows({"rows": [{"cells": [invalid]}]})
    assert response.status_code == 400, response.data
    assert not Row.objects.filter(dataset=dataset).exists()


def test_legacy_ids_cannot_replace_required_column_name(post_rows, dataset):
    cell = sdk_cell("input", "must not disappear")
    del cell["column_name"]
    response = post_rows({"rows": [sdk_row([cell])]})
    assert response.status_code == 400, response.data
    assert "column_name" in str(response.data)
    assert not Row.objects.filter(dataset=dataset).exists()
