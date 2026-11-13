import uuid
from pathlib import Path

import pytest
from rest_framework import status

from model_hub.models.choices import (
    CellStatus,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
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


@pytest.fixture
def compare_eval_metrics(compare_datasets, organization, workspace, user):
    template = EvalTemplate.objects.create(
        name="Compare score template",
        organization=organization,
        workspace=workspace,
        config={"output": "score"},
    )
    metrics = []
    for key in ("base_dataset", "other_dataset"):
        dataset = compare_datasets[key]
        dataset.column_order = list(
            Column.objects.filter(dataset=dataset).values_list("id", flat=True)
        )
        dataset.column_order = [str(column_id) for column_id in dataset.column_order]
        dataset.save(update_fields=["column_order"])
        metrics.append(
            UserEvalMetric.objects.create(
                name="Compare score",
                organization=organization,
                workspace=workspace,
                user=user,
                dataset=dataset,
                template=template,
                status=StatusType.INACTIVE.value,
            )
        )
    return metrics


@pytest.mark.django_db
def test_compare_start_evals_creates_columns(auth_client, compare_eval_metrics):
    base_metric, other_metric = compare_eval_metrics
    original_orders = {
        metric.dataset_id: list(metric.dataset.column_order)
        for metric in compare_eval_metrics
    }

    response = auth_client.post(
        f"/model-hub/datasets/{base_metric.dataset_id}/compare-datasets/start-eval/",
        {
            "user_eval_names": [base_metric.name],
            "dataset_ids": [str(other_metric.dataset_id)],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    for metric in compare_eval_metrics:
        metric.refresh_from_db()
        metric.dataset.refresh_from_db()
        assert metric.status == StatusType.NOT_STARTED.value
        column = Column.objects.get(dataset=metric.dataset, source_id=str(metric.id))
        reason = Column.objects.get(
            dataset=metric.dataset,
            source_id=f"{column.id}-sourceid-{metric.id}",
        )
        assert column.name == metric.name
        assert column.source == SourceChoices.EVALUATION.value
        assert column.data_type == DataTypeChoices.FLOAT.value
        assert reason.name == f"{metric.name}-reason"
        assert reason.source == SourceChoices.EVALUATION_REASON.value
        assert reason.data_type == DataTypeChoices.TEXT.value
        assert metric.dataset.column_order == [
            *original_orders[metric.dataset_id],
            str(column.id),
            str(reason.id),
        ]


@pytest.mark.django_db
def test_compare_start_evals_reuses_existing_columns(auth_client, compare_eval_metrics):
    base_metric, other_metric = compare_eval_metrics
    original_orders = {}
    reason_cells = []
    for metric in compare_eval_metrics:
        column = Column.objects.create(
            name=metric.name,
            dataset=metric.dataset,
            source_id=str(metric.id),
            source=SourceChoices.EVALUATION.value,
            data_type=DataTypeChoices.FLOAT.value,
        )
        reason = Column.objects.create(
            name=f"{metric.name}-reason",
            dataset=metric.dataset,
            source_id=f"{column.id}-sourceid-{metric.id}",
            source=SourceChoices.EVALUATION_REASON.value,
            data_type=DataTypeChoices.TEXT.value,
        )
        metric.dataset.column_order.extend([str(column.id), str(reason.id)])
        metric.dataset.save(update_fields=["column_order"])
        original_orders[metric.dataset_id] = list(metric.dataset.column_order)
        reason_cells.append(
            Cell.objects.create(
                dataset=metric.dataset,
                column=reason,
                row=Row.objects.get(dataset=metric.dataset),
                value="Previous reason",
                status=CellStatus.PASS.value,
            )
        )

    response = auth_client.post(
        f"/model-hub/datasets/{base_metric.dataset_id}/compare-datasets/start-eval/",
        {
            "user_eval_names": [base_metric.name],
            "dataset_ids": [str(other_metric.dataset_id)],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    for metric in compare_eval_metrics:
        metric.refresh_from_db()
        metric.dataset.refresh_from_db()
        assert metric.status == StatusType.NOT_STARTED.value
        assert metric.dataset.column_order == original_orders[metric.dataset_id]
        assert Column.objects.filter(dataset=metric.dataset).count() == 4
    for cell in reason_cells:
        cell.refresh_from_db()
        assert cell.status == CellStatus.RUNNING.value
        assert cell.value == "Previous reason"


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
    auth_client, monkeypatch, tmp_path
):
    compare_id = uuid.uuid4()
    monkeypatch.chdir(tmp_path)
    compare_dir = Path(get_compare_local_dir(compare_id))
    compare_dir.mkdir(parents=True)
    (compare_dir / "metadata.json").write_text("{}", encoding="utf-8")

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
