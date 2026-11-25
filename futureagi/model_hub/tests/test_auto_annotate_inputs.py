"""Regression coverage for categorical auto-annotation input lookups."""

import uuid

import pytest

from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.utils.auto_annotate import _get_input_values


@pytest.fixture
def input_cell(organization):
    dataset = Dataset.objects.create(
        name="Auto-annotation inputs", organization=organization
    )
    column = Column.objects.create(
        name="Input",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    return Cell.objects.create(
        dataset=dataset, column=column, row=row, value="First input"
    )


@pytest.mark.django_db
def test_input_values_follow_configured_column_order_for_requested_row(input_cell):
    column = Column.objects.create(
        name="Second input",
        dataset=input_cell.dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    Cell.objects.create(
        dataset=input_cell.dataset,
        column=column,
        row=input_cell.row,
        value="Second input",
    )
    other_row = Row.objects.create(dataset=input_cell.dataset, order=1)
    Cell.objects.create(
        dataset=input_cell.dataset,
        column=input_cell.column,
        row=other_row,
        value="Other row",
    )

    # Label settings store UUIDs as strings; ordering and repeated inputs matter.
    values = _get_input_values(
        [str(column.id), str(input_cell.column_id), str(column.id)],
        input_cell.dataset_id,
        input_cell.row_id,
    )

    assert values == ["Second input", "First input", "Second input"]


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["", "0", "Plain text", None])
def test_input_values_preserve_string_conversion(input_cell, value):
    input_cell.value = value
    input_cell.save()

    assert _get_input_values(
        [str(input_cell.column_id)], input_cell.dataset_id, input_cell.row_id
    ) == [str(value)]


@pytest.mark.django_db
def test_empty_inputs_need_no_cell_lookup(input_cell, django_assert_num_queries):
    with django_assert_num_queries(0):
        assert _get_input_values([], input_cell.dataset_id, input_cell.row_id) == []


@pytest.mark.django_db
@pytest.mark.parametrize("missing_scope", ["dataset_id", "row_id", "column_id"])
def test_input_values_do_not_fall_back_outside_requested_scope(
    input_cell, missing_scope
):
    ids = {
        "dataset_id": input_cell.dataset_id,
        "row_id": input_cell.row_id,
        "column_id": input_cell.column_id,
    }
    ids[missing_scope] = uuid.uuid4()

    with pytest.raises(Cell.DoesNotExist):
        _get_input_values([str(ids["column_id"])], ids["dataset_id"], ids["row_id"])
