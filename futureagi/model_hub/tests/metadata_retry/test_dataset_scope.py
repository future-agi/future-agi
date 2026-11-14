"""The retried column-type read stays scoped to the evaluated row's dataset."""

import json

import pytest
from django.db import connection

from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.tests.test_eval_metadata_retry import ReadFault, run_case
from model_hub.utils.eval_metadata import read_evaluation_column_types

pytestmark = pytest.mark.django_db(transaction=True)


def image_column(case, *, other_dataset):
    dataset = case.dataset
    if other_dataset:
        dataset = Dataset.objects.create(
            name="metadata-scope-other",
            organization=case.dataset.organization,
            workspace=case.workspace,
            user=case.user,
            column_order=[],
            column_config={},
        )
    return Column.objects.create(
        dataset=dataset,
        name="picture",
        data_type=DataTypeChoices.IMAGE.value,
        source=SourceChoices.OTHERS.value,
    )


def test_retried_column_types_ignore_other_dataset(evaluation_case):
    case = evaluation_case
    own_id = case.mappings["text"]
    other = image_column(case, other_dataset=True)
    fault = ReadFault("model_hub_column")
    with connection.execute_wrapper(fault):
        result = read_evaluation_column_types(
            [own_id, str(other.id)], dataset_id=case.dataset.id
        )
    assert fault.calls == 2
    assert result == {own_id: DataTypeChoices.TEXT.value}


@pytest.mark.parametrize("path", ["threaded", "direct"])
@pytest.mark.parametrize(
    ("other_dataset", "expected"), [(False, "image"), (True, "text")]
)
def test_runner_input_types_use_only_row_dataset_columns(
    evaluation_case, path, other_dataset, expected
):
    case = evaluation_case
    picture = image_column(case, other_dataset=other_dataset)
    case.mappings = {**case.mappings, "picture": str(picture.id)}
    case.runner.user_eval_metric.config["mapping"] = case.mappings
    fault = ReadFault("model_hub_column")
    with connection.execute_wrapper(fault):
        result = run_case(case, path)
    assert result["reason"] == "matched"
    assert fault.calls == 2
    input_types = json.loads(case.log.config)["input_data_types"]
    assert input_types == {"text": "text", "picture": expected}
