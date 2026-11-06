import uuid
import pytest

from model_hub.models.choices import DatasetSourceChoices, DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.utils.auto_annotate import _get_input_values

@pytest.mark.django_db
def test_get_values_resolves_cell_by_column(organization, workspace):
    dataset = Dataset.objects.create(
        name=f"auto-annote dataset {uuid.uuid4().hex[:8]}",
        source = DatasetSourceChoices.BUILD.value,
        organization=organization,
        workspace=workspace,
    )
    row = Row.objects.create(dataset=dataset, order=1)
    column = Column.objects.create(
        name = "input_col",
        data_type=DataTypeChoices.TEXT.value,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    Cell.objects.create(
        dataset=dataset, row=row, column=column, value="hello world"
    )
    result = _get_input_values([str(column.id)], dataset.id, row.id)

    assert result == ["hello world"]