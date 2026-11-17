"""The synthetic editor's additive choice must not reconcile away data.

Author these regressions before the implementation. On the pinned base the new
operation is rejected by the serializer; bypassing that contract would enter the
legacy destructive reconciliation. No provider or task worker is used here.
"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.models.choices import CellStatus, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.views.datasets.create import synthetic
from tfc.constants.api_calls import APICallStatusChoices

pytestmark = pytest.mark.django_db


@pytest.fixture
def tasks(monkeypatch):
    result = SimpleNamespace(
        rows=Mock(), columns=Mock(), replace=Mock(), manager=Mock(), charge=Mock()
    )
    result.manager.start_task.return_value = "test-request"
    result.charge.return_value = SimpleNamespace(
        status=APICallStatusChoices.SUCCESS.value, save=Mock()
    )
    monkeypatch.setattr(synthetic.generate_new_rows, "delay", result.rows)
    monkeypatch.setattr(synthetic.generate_new_columns, "delay", result.columns)
    monkeypatch.setattr(synthetic.create_synthetic_dataset, "delay", result.replace)
    monkeypatch.setattr(synthetic, "SyntheticTaskManager", lambda: result.manager)
    monkeypatch.setattr(synthetic, "log_and_deduct_cost_for_resource_request", result.charge)
    monkeypatch.setattr(synthetic, "ROW_LIMIT_REACHED_MESSAGE", "Row limit reached")
    return result


@pytest.fixture
def seed(user, workspace, organization):
    def create(count=20):
        config = {
            "dataset": {
                "name": "Synthetic append target",
                "description": "Support questions",
                "objective": "Support",
                "patterns": "Short",
            },
            "num_rows": count,
            "columns": [{
                "name": "question", "data_type": "text", "description": "A question",
                "property": {"min_length": 2},
            }],
        }
        dataset = Dataset.objects.create(
            name=config["dataset"]["name"], organization=organization,
            workspace=workspace, user=user, synthetic_dataset_config=config,
        )
        columns = [
            Column.objects.create(
                dataset=dataset, name=name, data_type="text", source=source
            )
            for name, source in [
                ("question", SourceChoices.OTHERS.value),
                ("review_notes", SourceChoices.OTHERS.value),
                ("evaluation", SourceChoices.EXPERIMENT_EVALUATION.value),
            ]
        ]
        dataset.column_order = [str(col.id) for col in columns]
        dataset.column_config = {
            str(col.id): {"is_visible": i != 2, "is_frozen": i == 1}
            for i, col in enumerate(columns)
        }
        dataset.save()
        for i in range(count):
            row = Row.objects.create(dataset=dataset, order=i * 2)
            for col in columns:
                Cell.objects.create(
                    dataset=dataset, row=row, column=col,
                    value=f"saved-{col.name}-{i}", status=CellStatus.PASS.value,
                    feedback_info={"reviewed": True},
                )
        return dataset, {
            **deepcopy(config), "num_rows": 10, "generation_mode": "append_synthetic"
        }
    return create


@pytest.fixture
def put(user, workspace, organization):
    def send(dataset, payload):
        request = APIRequestFactory().put(
            f"/model-hub/develops/{dataset.id}/update-synthetic-config/",
            payload, format="json",
        )
        force_authenticate(request, user=user)
        request.workspace = workspace
        request.organization = organization
        return synthetic.UpdateSyntheticDatasetConfigView.as_view()(
            request, dataset_id=dataset.id
        )
    return send


def snapshot(dataset):
    dataset.refresh_from_db()
    return {
        "rows": list(Row.objects.filter(dataset=dataset).order_by("id").values(
            "id", "order", "deleted"
        )),
        "columns": list(Column.objects.filter(dataset=dataset).order_by("id").values(
            "id", "name", "data_type", "source", "deleted"
        )),
        "cells": list(Cell.objects.filter(dataset=dataset).order_by("id").values(
            "id", "row_id", "column_id", "value", "status", "deleted", "feedback_info"
        )),
        "order": dataset.column_order,
        "column_config": dataset.column_config,
        "synthetic_config": dataset.synthetic_dataset_config,
    }


@pytest.mark.parametrize("existing_count", [20, 10, 5])
def test_append_preserves_rows_columns_and_reviewed_cells(
    seed, put, tasks, django_capture_on_commit_callbacks, existing_count
):
    dataset, payload = seed(existing_count)
    before = snapshot(dataset)
    old_row_ids = {row["id"] for row in before["rows"]}
    with django_capture_on_commit_callbacks(execute=True):
        response = put(dataset, payload)
    assert response.status_code == 200, response.data
    after = snapshot(dataset)
    assert len(after["rows"]) == existing_count + 10
    assert [row for row in after["rows"] if row["id"] in old_row_ids] == before["rows"]
    assert after["columns"] == before["columns"]
    assert after["order"] == before["order"]
    assert after["column_config"] == before["column_config"]
    assert [cell for cell in after["cells"] if cell["row_id"] in old_row_ids] == before["cells"]
    assert all(not row["deleted"] for row in after["rows"])
    assert all(not cell["deleted"] for cell in after["cells"])
    added = [row for row in after["rows"] if row["id"] not in old_row_ids]
    assert min(row["order"] for row in added) > max(row["order"] for row in before["rows"])
    assert len(after["cells"]) == (existing_count + 10) * 3
    for cell in Cell.objects.filter(dataset=dataset).exclude(row_id__in=old_row_ids).exclude(column__name="question"):
        assert cell.value in (None, "")
        assert cell.status == CellStatus.PASS.value
    tasks.rows.assert_called_once()
    kwargs = tasks.rows.call_args.kwargs
    assert kwargs["validated_data"]["num_rows"] == 10
    assert set(kwargs["new_rows_id"]) == {str(row["id"]) for row in added}
    assert kwargs["gen_columns"] == payload["columns"]
    assert tasks.charge.call_args.kwargs["config"] == {"total_rows": 10}
    tasks.columns.assert_not_called()
    tasks.replace.assert_not_called()


def test_append_new_column_leaves_old_rows_blank_and_only_generates_new_rows(
    seed, put, tasks, django_capture_on_commit_callbacks
):
    dataset, payload = seed()
    old_ids = set(Row.objects.filter(dataset=dataset).values_list("id", flat=True))
    payload["columns"].append({
        "name": "answer", "data_type": "text", "description": "An answer", "property": {}
    })
    with django_capture_on_commit_callbacks(execute=True):
        response = put(dataset, payload)
    assert response.status_code == 200, response.data
    column = Column.objects.get(dataset=dataset, name="answer", deleted=False)
    old_cells = Cell.objects.filter(column=column, row_id__in=old_ids)
    assert old_cells.count() == 20
    assert all(cell.value in (None, "") and cell.status == CellStatus.PASS.value for cell in old_cells)
    new_cells = Cell.objects.filter(column=column).exclude(row_id__in=old_ids)
    assert new_cells.count() == 10
    assert all(cell.status == CellStatus.RUNNING.value for cell in new_cells)
    assert not old_ids.intersection(tasks.rows.call_args.kwargs["new_rows_id"])
    tasks.rows.assert_called_once()
    tasks.columns.assert_not_called()
    dataset.refresh_from_db()
    assert str(column.id) == dataset.column_order[-1]
    assert dataset.column_config[str(column.id)]["is_visible"] is True


def test_append_rolls_back_partial_writes_and_does_not_dispatch(
    seed, put, tasks, monkeypatch, django_capture_on_commit_callbacks
):
    dataset, payload = seed()
    before = snapshot(dataset)
    payload["columns"].append({
        "name": "answer", "data_type": "text", "description": "An answer", "property": {}
    })
    fail = Mock(side_effect=RuntimeError("controlled cell write failure"))
    monkeypatch.setattr(Cell.objects, "bulk_create", fail)
    with django_capture_on_commit_callbacks(execute=True):
        response = put(dataset, payload)
    assert response.status_code == 500
    fail.assert_called_once()
    assert snapshot(dataset) == before
    tasks.rows.assert_not_called()
    tasks.columns.assert_not_called()
    tasks.replace.assert_not_called()


def test_append_checks_row_capacity_before_mutation(seed, put, tasks):
    dataset, payload = seed()
    before = snapshot(dataset)
    tasks.charge.return_value = None
    response = put(dataset, payload)
    assert response.status_code == 429, response.data
    assert snapshot(dataset) == before
    tasks.rows.assert_not_called()


def test_replace_still_replaces_instead_of_appending(seed, put, tasks):
    dataset, payload = seed()
    before_ids = set(Row.objects.filter(dataset=dataset).values_list("id", flat=True))
    payload.pop("generation_mode")
    payload["regenerate"] = True
    response = put(dataset, payload)
    assert response.status_code == 200, response.data
    live_rows = Row.objects.filter(dataset=dataset, deleted=False)
    assert live_rows.count() == 10
    assert not before_ids.intersection(live_rows.values_list("id", flat=True))
    assert list(Column.objects.filter(dataset=dataset, deleted=False).values_list("name", flat=True)) == ["question"]
    tasks.replace.assert_called_once()
    tasks.rows.assert_not_called()


def test_legacy_config_update_keeps_its_final_size_semantics(seed, put, tasks):
    dataset, payload = seed()
    payload.pop("generation_mode")
    response = put(dataset, payload)
    assert response.status_code == 200, response.data
    assert Row.objects.filter(dataset=dataset, deleted=False).count() == 10
    assert list(Column.objects.filter(dataset=dataset, deleted=False).values_list("name", flat=True)) == ["question"]
    tasks.rows.assert_not_called()
    tasks.replace.assert_not_called()


def test_append_and_replace_cannot_be_requested_together(seed, put, tasks):
    dataset, payload = seed()
    before = snapshot(dataset)
    response = put(dataset, {**payload, "regenerate": True})
    assert response.status_code == 400, response.data
    assert snapshot(dataset) == before
    tasks.rows.assert_not_called()
    tasks.replace.assert_not_called()


def test_existing_model_append_route_keeps_its_regeneration_contract(
    seed, put, tasks, monkeypatch
):
    from model_hub.views.datasets.add_rows import append_generation

    dataset, payload = seed()
    adapter = Mock(return_value=Response({"status": True}, status=200))
    monkeypatch.setattr(append_generation, "append_generation_response", adapter)
    response = put(dataset, {**payload, "generation_mode": "append", "regenerate": True})
    assert response.status_code == 200, response.data
    adapter.assert_called_once()
    assert adapter.call_args.kwargs == {"regenerate": True}
    tasks.rows.assert_not_called()
    tasks.replace.assert_not_called()
