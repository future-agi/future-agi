import json
import uuid
from pathlib import Path

import pytest
from rest_framework import status

from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.views.develop_dataset import CompareDatasetsView
from tfc.utils.storage import get_compare_local_dir


@pytest.fixture
def compare_datasets(db, organization, workspace):
    base_dataset = Dataset.objects.create(
        name="Base Compare Dataset",
        organization=organization,
        workspace=workspace,
    )
    other_dataset = Dataset.objects.create(
        name="Other Compare Dataset",
        organization=organization,
        workspace=workspace,
    )

    base_input = Column.objects.create(
        name="input",
        dataset=base_dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    other_input = Column.objects.create(
        name="input",
        dataset=other_dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    base_eval = Column.objects.create(
        name="judge",
        dataset=base_dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION.value,
        source_id=None,
    )
    other_eval = Column.objects.create(
        name="judge",
        dataset=other_dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION.value,
        source_id=None,
    )

    base_row = Row.objects.create(dataset=base_dataset, order=0)
    other_row = Row.objects.create(dataset=other_dataset, order=0)
    Cell.objects.create(
        dataset=base_dataset,
        column=base_input,
        row=base_row,
        value="alpha",
    )
    Cell.objects.create(
        dataset=other_dataset,
        column=other_input,
        row=other_row,
        value="alpha",
    )
    Cell.objects.create(
        dataset=base_dataset,
        column=base_eval,
        row=base_row,
        value="pass",
    )
    Cell.objects.create(
        dataset=other_dataset,
        column=other_eval,
        row=other_row,
        value="fail",
    )

    return {
        "base_dataset": base_dataset,
        "other_dataset": other_dataset,
        "base_input": base_input,
        "other_input": other_input,
        "base_eval": base_eval,
        "other_eval": other_eval,
        "base_row": base_row,
        "other_row": other_row,
    }


@pytest.mark.django_db
def test_compare_delete_route_get_is_method_guarded(auth_client):
    response = auth_client.get(f"/model-hub/datasets/delete-compare/{uuid.uuid4()}/")

    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert response.json()["detail"] == "Use DELETE to remove compare dataset files."


@pytest.mark.django_db
def test_compare_row_route_delete_is_method_guarded(auth_client):
    response = auth_client.delete(
        f"/model-hub/datasets/get-compare-row/{uuid.uuid4()}/{uuid.uuid4()}/"
    )

    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert (
        response.json()["detail"]
        == "Use DELETE /datasets/delete-compare/{compare_id}/ to remove compare dataset files."
    )


@pytest.mark.django_db
def test_compare_delete_falls_back_to_synchronous_cleanup(
    auth_client, monkeypatch, tmp_path, compare_datasets
):
    compare_id = uuid.uuid4()
    monkeypatch.chdir(tmp_path)
    compare_dir = Path(get_compare_local_dir(compare_id))
    compare_dir.mkdir(parents=True)
    (compare_dir / "metadata.json").write_text(
        json.dumps({
            "base_dataset_id": str(compare_datasets["base_dataset"].id),
            "comparison_datasets": [
                str(compare_datasets["base_dataset"].id),
                str(compare_datasets["other_dataset"].id),
            ],
        }),
        encoding="utf-8",
    )

    def raise_temporal_unavailable(*args, **kwargs):
        raise RuntimeError("Temporal unavailable")

    monkeypatch.setattr(
        "tfc.temporal.drop_in.start_activity",
        raise_temporal_unavailable,
    )
    monkeypatch.setattr(
        "model_hub.views.develop_dataset.delete_compare_folder",
        lambda compare_id: None,
    )

    response = auth_client.delete(f"/model-hub/datasets/delete-compare/{compare_id}/")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["result"] == {"message": "File(s) deleted successfully"}
    assert compare_dir.resolve().parent == tmp_path / "media" / "compare"
    assert not compare_dir.exists()


@pytest.mark.django_db
def test_compare_pagination_tolerates_eval_columns_without_source_id(
    compare_datasets, monkeypatch
):
    base_dataset = compare_datasets["base_dataset"]
    other_dataset = compare_datasets["other_dataset"]
    base_input = compare_datasets["base_input"]
    base_eval = compare_datasets["base_eval"]
    other_eval = compare_datasets["other_eval"]
    base_row = compare_datasets["base_row"]
    other_row = compare_datasets["other_row"]

    def fake_download_json_from_s3(object_key):
        return {
            "column_config": [
                {
                    "id": str(base_input.id),
                    "name": "input",
                    "data_type": DataTypeChoices.TEXT.value,
                }
            ],
            "table": [
                {
                    "row_id": str(base_row.id),
                    str(base_input.id): {
                        "cell_value": "alpha",
                        "cell_row_id": str(base_row.id),
                    },
                }
            ],
        }

    monkeypatch.setattr(
        "model_hub.views.develop_dataset.download_json_from_s3",
        fake_download_json_from_s3,
    )

    view = CompareDatasetsView()
    view.process_base_values = lambda *args, **kwargs: (True, {})
    columns_qs = Column.objects.filter(dataset__in=[base_dataset, other_dataset])
    dynamic_sources = [
        SourceChoices.RUN_PROMPT.value,
        SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
        SourceChoices.EVALUATION_TAGS.value,
        SourceChoices.OPTIMISATION_EVALUATION_TAGS.value,
        SourceChoices.EVALUATION.value,
        SourceChoices.EXPERIMENT_EVALUATION.value,
        SourceChoices.OPTIMISATION_EVALUATION.value,
        SourceChoices.EVALUATION_REASON.value,
    ]

    result = view.get_paginated_compare_json(
        compare_id=uuid.uuid4(),
        start=0,
        end=1,
        start_page=1,
        end_page=2,
        common_columns={"judge"},
        comparison_datasets=[base_dataset, other_dataset],
        columns_lookup={
            (base_dataset.id, "judge"): base_eval,
            (other_dataset.id, "judge"): other_eval,
        },
        dataset_id=base_dataset.id,
        columns_qs=columns_qs,
        common_base_values=["alpha"],
        dataset_info={
            "alpha": {
                str(base_dataset.id): str(base_row.id),
                str(other_dataset.id): str(other_row.id),
            }
        },
        base_column_name="input",
        dynamic_sources=dynamic_sources,
        result={},
    )

    eval_configs = [
        column
        for column in result["column_config"]
        if column.get("origin_type") == SourceChoices.EVALUATION.value
    ]
    assert len(eval_configs) == 2
    assert {column["source_id"] for column in eval_configs} == {None}
    assert result["table"][0][str(base_eval.id)]["cell_value"] == "pass"
    assert result["table"][0][str(other_eval.id)]["cell_value"] == "fail"


@pytest.mark.django_db(transaction=True)
def test_compare_matches_four_questions_by_value_not_row_id(compare_datasets):
    questions = [
        "What is the capital of Japan?",
        "What is 2 + 2?",
        "Name a primary color.",
        "What is the opposite of hot?",
    ]
    expected = {question: {} for question in questions}
    datasets = []
    base_columns = {}
    for prefix in ("base", "other"):
        dataset = compare_datasets[f"{prefix}_dataset"]
        column = compare_datasets[f"{prefix}_input"]
        datasets.append(dataset)
        base_columns[dataset.id] = column
        for index, question in enumerate(questions):
            if index == 0:
                row = compare_datasets[f"{prefix}_row"]
                Cell.objects.filter(column=column, row=row).update(value=question)
            else:
                row = Row.objects.create(dataset=dataset, order=index)
                Cell.objects.create(
                    dataset=dataset, column=column, row=row, value=question
                )
            expected[question][str(dataset.id)] = str(row.id)

    result = CompareDatasetsView().generate_dataset_info(datasets, base_columns)

    assert result == expected
    assert all(len(set(row_map.values())) == 2 for row_map in result.values())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("left_value", "right_value", "matches"),
    [
        ("  Keep Case  ", "  Keep Case  ", True),
        ("Question", "question", False),
        ("Question", " Question ", False),
        (42, "42", True),
        ("042", 42, False),
        ("42.0", 42, False),
    ],
)
def test_compare_preserves_exact_stored_value_matching(
    compare_datasets, left_value, right_value, matches
):
    # Cell.value is a TextField: numeric inputs are stored as text. Comparison
    # must not silently case-fold, trim, or numerically coerce stored strings.
    datasets = []
    base_columns = {}
    for prefix, value in (("base", left_value), ("other", right_value)):
        dataset = compare_datasets[f"{prefix}_dataset"]
        column = compare_datasets[f"{prefix}_input"]
        Cell.objects.filter(column=column).update(value=value)
        datasets.append(dataset)
        base_columns[dataset.id] = column

    result = CompareDatasetsView().generate_dataset_info(datasets, base_columns)

    if matches:
        assert result == {
            str(left_value): {
                str(dataset.id): str(compare_datasets[f"{prefix}_row"].id)
                for prefix, dataset in zip(("base", "other"), datasets)
            }
        }
    else:
        assert result == {}


@pytest.mark.django_db(transaction=True)
def test_compare_empty_result_keeps_real_column_configuration(
    auth_client, compare_datasets, monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    base_dataset = compare_datasets["base_dataset"]
    other_dataset = compare_datasets["other_dataset"]
    Cell.objects.filter(column=compare_datasets["other_input"]).update(value="beta")
    for dataset in (base_dataset, other_dataset):
        Column.objects.create(
            dataset=dataset,
            name="answer",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )

    response = auth_client.post(
        f"/model-hub/datasets/{base_dataset.id}/compare-datasets/",
        {
            "dataset_ids": [str(other_dataset.id)],
            "base_column_name": "input",
            "page_size": 10,
            "current_page_index": 0,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    assert result["table"] == []
    assert result["metadata"]["total_rows"] == 0
    assert {column["group"]["name"] for column in result["column_config"]} == {
        "input",
        "answer",
    }
