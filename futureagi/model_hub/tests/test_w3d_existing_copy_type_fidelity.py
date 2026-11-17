"""Authenticated copy regressions; run unchanged against each pinned base first."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.choices import CellStatus, DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


@pytest.fixture
def copy_dataset(organization, workspace):
    def create(name, data_type, *, target_workspace=workspace):
        dataset = Dataset.objects.create(
            name=name, organization=organization, workspace=target_workspace
        )
        column = Column.objects.create(
            name="amount",
            dataset=dataset,
            data_type=data_type,
            source=SourceChoices.OTHERS.value,
        )
        return dataset, column

    return create


@pytest.fixture
def usage_call(monkeypatch):
    call = Mock(return_value=SimpleNamespace(status="created", save=Mock()))
    monkeypatch.setattr(
        "model_hub.views.datasets.add_rows.existing_dataset."
        "log_and_deduct_cost_for_resource_request",
        call,
    )
    return call


def _add_cell(dataset, column, value, *, order=0, cell_status=CellStatus.PASS.value):
    row = Row.objects.create(dataset=dataset, order=order)
    return Cell.objects.create(
        dataset=dataset,
        row=row,
        column=column,
        value=value,
        value_infos={"note": "keep exactly"},
        status=cell_status,
    )


def _post_copy(client, source, target, mapping):
    return client.post(
        f"/model-hub/develops/{target.id}/add_rows_from_existing_dataset/",
        {"source_dataset_id": str(source.id), "column_mapping": mapping},
        format="json",
    )


def _target_state(dataset):
    return (
        list(
            Row.all_objects.filter(dataset=dataset)
            .order_by("id")
            .values_list("id", "order", "deleted")
        ),
        list(
            Cell.all_objects.filter(dataset=dataset)
            .order_by("id")
            .values_list("id", "row_id", "column_id", "value", "status", "value_infos")
        ),
    )


def _assert_rejected(response, target, before, usage_call):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _target_state(target) == before
    usage_call.assert_not_called()


@pytest.mark.parametrize("value", ["12.75", "-12.75", "0.5", "12.000000000000001"])
def test_fractional_float_to_integer_is_rejected_before_usage_or_writes(
    auth_client, copy_dataset, usage_call, value
):
    source, source_column = copy_dataset("Source", DataTypeChoices.FLOAT.value)
    target, target_column = copy_dataset("Target", DataTypeChoices.INTEGER.value)
    _add_cell(source, source_column, "12", order=0)
    _add_cell(source, source_column, value, order=1)
    _add_cell(target, target_column, "7", order=4)
    before = _target_state(target)

    response = _post_copy(
        auth_client, source, target, {str(source_column.id): str(target_column.id)}
    )

    _assert_rejected(response, target, before, usage_call)
    message = str(response.json()).lower()
    for expected in ("amount", "float", "integer", "same type"):
        assert expected in message
    target_column.refresh_from_db()
    assert target_column.data_type == DataTypeChoices.INTEGER.value


def test_invalid_value_after_first_batch_rejects_the_entire_copy(
    auth_client, copy_dataset, usage_call
):
    source, source_column = copy_dataset("Source", DataTypeChoices.FLOAT.value)
    target, target_column = copy_dataset("Target", DataTypeChoices.INTEGER.value)
    rows = [Row(dataset=source, order=index) for index in range(1000)]
    Row.objects.bulk_create(rows)
    Cell.objects.bulk_create(
        [
            Cell(dataset=source, row=row, column=source_column, value="12")
            for row in rows
        ]
    )
    _add_cell(source, source_column, "12.75", order=1000)
    before = _target_state(target)

    response = _post_copy(
        auth_client, source, target, {str(source_column.id): str(target_column.id)}
    )

    _assert_rejected(response, target, before, usage_call)


@pytest.mark.parametrize(
    "value",
    ["1e3", "0x10", "NaN", "Infinity", "12abc", "1_000", "9007199254740993"],
)
def test_float_to_integer_rejects_values_the_grid_cannot_display_losslessly(
    auth_client, copy_dataset, usage_call, value
):
    source, source_column = copy_dataset("Source", DataTypeChoices.FLOAT.value)
    target, target_column = copy_dataset("Target", DataTypeChoices.INTEGER.value)
    _add_cell(source, source_column, value)
    before = _target_state(target)

    response = _post_copy(
        auth_client, source, target, {str(source_column.id): str(target_column.id)}
    )

    _assert_rejected(response, target, before, usage_call)


@pytest.mark.parametrize(
    ("source_type", "target_type", "value"),
    [
        ("float", "float", "12.75"),
        ("text", "text", "  Exact text, café\nsecond line  "),
        ("integer", "integer", "12"),
        ("json", "json", '{"amount": 12.75}'),
        ("array", "array", '["a", "b"]'),
        ("boolean", "boolean", "true"),
        ("float", "integer", "12"),
        ("float", "integer", "12.0"),
        ("float", "integer", "-4.000"),
        ("float", "integer", " +0012.00 "),
        ("float", "integer", "9007199254740991"),
        ("float", "integer", ""),
        ("float", "integer", None),
        ("integer", "float", "12"),
        ("integer", "float", "-4"),
        ("integer", "float", "0"),
        ("integer", "float", ""),
        ("integer", "float", None),
    ],
)
def test_supported_copies_preserve_values_status_and_metadata(
    auth_client, copy_dataset, usage_call, source_type, target_type, value
):
    source, source_column = copy_dataset("Source", source_type)
    target, target_column = copy_dataset("Target", target_type)
    source_cell = _add_cell(source, source_column, value)

    response = _post_copy(
        auth_client, source, target, {str(source_column.id): str(target_column.id)}
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["result"]["rows_added"] == 1
    assert Row.objects.filter(dataset=target).count() == 1
    copied = Cell.objects.get(dataset=target, column=target_column)
    assert copied.value == source_cell.value
    assert copied.status == source_cell.status
    assert copied.value_infos == source_cell.value_infos
    target_column.refresh_from_db()
    assert target_column.data_type == target_type
    usage_call.assert_called_once()


@pytest.mark.parametrize("cell_status", [CellStatus.ERROR.value, CellStatus.RUNNING.value])
def test_same_type_copy_preserves_non_pass_cells(
    auth_client, copy_dataset, usage_call, cell_status
):
    source, source_column = copy_dataset("Source", DataTypeChoices.FLOAT.value)
    target, target_column = copy_dataset("Target", DataTypeChoices.FLOAT.value)
    source_cell = _add_cell(source, source_column, "No result", cell_status=cell_status)

    response = _post_copy(
        auth_client, source, target, {str(source_column.id): str(target_column.id)}
    )

    assert response.status_code == status.HTTP_200_OK
    copied = Cell.objects.get(dataset=target, column=target_column)
    assert (copied.value, copied.status) == (source_cell.value, cell_status)
    usage_call.assert_called_once()


@pytest.mark.parametrize(
    ("source_type", "target_type"),
    [("text", "integer"), ("text", "float"), ("json", "array"), ("image", "boolean")],
)
@pytest.mark.parametrize("has_row", [True, False])
def test_unsupported_mapping_is_refused_even_without_source_rows(
    auth_client, copy_dataset, usage_call, source_type, target_type, has_row
):
    source, source_column = copy_dataset("Source", source_type)
    target, target_column = copy_dataset("Target", target_type)
    if has_row:
        _add_cell(source, source_column, "12")
    before = _target_state(target)

    response = _post_copy(
        auth_client, source, target, {str(source_column.id): str(target_column.id)}
    )

    _assert_rejected(response, target, before, usage_call)
    assert "same type" in str(response.json()).lower()


@pytest.mark.parametrize(
    "case", ["foreign_source", "foreign_target", "duplicate_target", "invalid_id", "empty"]
)
def test_invalid_mapping_keeps_target_unchanged(
    auth_client, copy_dataset, usage_call, case
):
    source, source_column = copy_dataset("Source", DataTypeChoices.FLOAT.value)
    target, target_column = copy_dataset("Target", DataTypeChoices.FLOAT.value)
    _outside, outside_column = copy_dataset("Outside", DataTypeChoices.FLOAT.value)
    _add_cell(source, source_column, "12.75")
    _add_cell(target, target_column, "7")
    mapping = {str(source_column.id): str(target_column.id)}
    if case == "foreign_source":
        mapping = {str(outside_column.id): str(target_column.id)}
    elif case == "foreign_target":
        mapping = {str(source_column.id): str(outside_column.id)}
    elif case == "duplicate_target":
        second_column = Column.objects.create(
            name="Second", dataset=source, data_type="float", source=SourceChoices.OTHERS.value
        )
        mapping[str(second_column.id)] = str(target_column.id)
    elif case == "invalid_id":
        mapping = {"not-a-uuid": str(target_column.id)}
    elif case == "empty":
        mapping = {}
    before = _target_state(target)

    response = _post_copy(auth_client, source, target, mapping)

    _assert_rejected(response, target, before, usage_call)


def test_other_workspace_source_stays_refused(
    auth_client, copy_dataset, usage_call, organization, user
):
    other_workspace = Workspace.objects.create(
        name="Outside copy scope", organization=organization, created_by=user
    )
    source, source_column = copy_dataset(
        "Outside source", DataTypeChoices.FLOAT.value, target_workspace=other_workspace
    )
    target, target_column = copy_dataset("Target", DataTypeChoices.INTEGER.value)
    _add_cell(source, source_column, "12")
    before = _target_state(target)

    response = _post_copy(
        auth_client, source, target, {str(source_column.id): str(target_column.id)}
    )

    _assert_rejected(response, target, before, usage_call)


def test_numeric_validation_ignores_deleted_and_unmapped_cells(
    auth_client, copy_dataset, usage_call
):
    source, source_column = copy_dataset("Source", DataTypeChoices.FLOAT.value)
    target, target_column = copy_dataset("Target", DataTypeChoices.INTEGER.value)
    valid_cell = _add_cell(source, source_column, "12.0")
    deleted_row_cell = _add_cell(source, source_column, "12.75", order=1)
    Row.objects.filter(id=deleted_row_cell.row_id).update(deleted=True)
    deleted_cell = Cell.objects.create(
        dataset=source, row=valid_cell.row, column=source_column, value="12.75"
    )
    Cell.objects.filter(id=deleted_cell.id).update(deleted=True)
    unmapped_column = Column.objects.create(
        name="Unmapped", dataset=source, data_type="float", source=SourceChoices.OTHERS.value
    )
    Cell.objects.create(
        dataset=source, row=valid_cell.row, column=unmapped_column, value="12.75"
    )

    response = _post_copy(
        auth_client, source, target, {str(source_column.id): str(target_column.id)}
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["result"]["rows_added"] == 1
    assert Cell.objects.get(dataset=target, column=target_column).value == "12.0"
    usage_call.assert_called_once()
