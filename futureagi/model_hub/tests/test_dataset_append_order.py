"""H4: append after the greatest persisted order, including soft-deleted rows.

These are database regressions, not mocked-manager assertions. Providers and
background dispatch are stubbed, but the public routes and row writes are real.
The non_monotonic case also catches treating newest-created as highest-order.
"""

import uuid
from contextlib import nullcontext
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from model_hub.models.choices import CellStatus, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.tests.network_isolation import no_external_lookups  # noqa: F401

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_external_lookups")]
STATES = ["tail", "only_deleted", "non_monotonic"]


def row_snapshot(dataset):
    return list(
        Row.all_objects.filter(dataset=dataset)
        .order_by("id")
        .values("id", "order", "deleted", "deleted_at", "metadata")
    )


def cell_snapshot(dataset):
    return list(
        Cell.all_objects.filter(dataset=dataset)
        .order_by("id")
        .values(
            "id", "row_id", "column_id", "value", "status", "deleted", "feedback_info"
        )
    )


@pytest.fixture
def make_grid(organization, workspace, user):
    def make(state="tail", *, names=("question",), count=None):
        dataset = Dataset.objects.create(
            name=f"Append order {uuid.uuid4().hex}",
            organization=organization,
            workspace=workspace,
            user=user,
            column_order=[],
            column_config={},
        )
        columns = [
            Column.objects.create(
                dataset=dataset,
                name=name,
                data_type="text",
                source=SourceChoices.OTHERS.value,
            )
            for name in names
        ]
        dataset.column_order = [str(col.id) for col in columns]
        dataset.column_config = {
            str(col.id): {"is_visible": True, "is_frozen": False} for col in columns
        }
        dataset.save()
        orders = {
            "tail": [0, 1],
            "only_deleted": [0],
            "non_monotonic": [1, 0],
            "source": [0, 1],
        }[state]
        if count is not None:
            orders = list(range(count))
        rows = []
        for i, order in enumerate(orders):
            row = Row.objects.create(dataset=dataset, order=order)
            # Make timestamp ordering deterministic and deliberately independent
            # of order in the non_monotonic case, without sleeping.
            Row.objects.filter(id=row.id).update(
                created_at=timezone.now() - timedelta(days=len(orders) - i)
            )
            rows.append(row)
            for col in columns:
                Cell.objects.create(
                    dataset=dataset,
                    row=row,
                    column=col,
                    value=f"saved-{col.name}-{order}",
                    status=CellStatus.PASS.value,
                    feedback_info={"reviewed": True},
                )
        if state != "source":
            tail = max(rows, key=lambda row: row.order)
            now = timezone.now()
            Row.objects.filter(id=tail.id).update(deleted=True, deleted_at=now)
            Cell.objects.filter(row=tail).update(deleted=True, deleted_at=now)
        before = row_snapshot(dataset)
        return SimpleNamespace(
            dataset=dataset,
            columns=columns,
            before=before,
            cells=cell_snapshot(dataset),
            start=max(row["order"] for row in before) + 1,
        )

    return make


@pytest.fixture(autouse=True)
def stub_dispatch_and_billing(monkeypatch):
    from model_hub.views import develop_dataset
    from model_hub.views.datasets.add_rows import existing_dataset, huggingface
    from model_hub.views.datasets.add_rows import synthetic as add_synthetic
    from model_hub.views.datasets.create import synthetic as edit_synthetic

    charge = Mock(return_value=SimpleNamespace(status="success", save=Mock()))
    for module in (develop_dataset, existing_dataset, huggingface, edit_synthetic):
        monkeypatch.setattr(module, "log_and_deduct_cost_for_resource_request", charge)
    tasks = SimpleNamespace(rows=Mock(), columns=Mock(), replace=Mock(), manager=Mock())
    for module in (add_synthetic, edit_synthetic):
        monkeypatch.setattr(module.generate_new_rows, "delay", tasks.rows)
        monkeypatch.setattr(module.generate_new_columns, "delay", tasks.columns)
    monkeypatch.setattr(edit_synthetic.create_synthetic_dataset, "delay", tasks.replace)
    monkeypatch.setattr(edit_synthetic, "SyntheticTaskManager", lambda: tasks.manager)
    return tasks


def assert_appended(grid, count):
    rows = row_snapshot(grid.dataset)
    old_ids = {row["id"] for row in grid.before}
    assert [row for row in rows if row["id"] in old_ids] == grid.before
    added = [row for row in rows if row["id"] not in old_ids]
    assert len(added) == count
    assert sorted(row["order"] for row in added) == list(
        range(grid.start, grid.start + count)
    )
    assert all(not row["deleted"] for row in added)
    orders = [row["order"] for row in rows]
    assert len(orders) == len(set(orders)), "New orders collide with persisted rows"
    assert [
        cell for cell in cell_snapshot(grid.dataset) if cell["row_id"] in old_ids
    ] == grid.cells
    return added


def config_for(grid, count=2):
    return {
        "generation_mode": "append",
        "model": "gpt-4o-mini",
        "num_rows": count,
        "columns": [
            {
                "name": col.name,
                "data_type": "text",
                "description": col.name,
                "property": [],
            }
            for col in grid.columns
        ],
        "dataset": {
            "name": grid.dataset.name,
            "description": "Support questions",
            "objective": "Support",
            "patterns": "Short",
        },
    }


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("route", ["generate", "regenerate"])
def test_generated_append_reserves_deleted_orders(
    auth_client, make_grid, monkeypatch, state, route
):
    from model_hub.services import row_generation

    grid = make_grid(state)
    config = config_for(grid)
    generate = Mock(return_value=[{"question": "first"}, {"question": "second"}])
    monkeypatch.setattr(row_generation, "generate_batch", generate)
    if route == "generate":
        response = auth_client.post(
            f"/model-hub/develops/{grid.dataset.id}/add_synthetic_data/",
            config,
            format="json",
        )
    else:
        # Save the same canonical config as Generate, without an earlier append
        # that could hide which request allocated the colliding order.
        grid.dataset.dataset_config = {
            "row_generation": row_generation.for_dataset(
                row_generation.validate_append_config(config), grid.dataset
            )
        }
        grid.dataset.save(update_fields=["dataset_config"])
        response = auth_client.put(
            f"/model-hub/develops/{grid.dataset.id}/update-synthetic-config/",
            {**config, "regenerate": True},
            format="json",
        )
    assert response.status_code == 200, response.data
    generate.assert_called_once()
    added = assert_appended(grid, 2)
    assert list(
        Cell.objects.filter(row_id__in=[r["id"] for r in added])
        .order_by("row__order")
        .values_list("value", flat=True)
    ) == ["first", "second"]


@pytest.mark.parametrize("state", STATES)
def test_legacy_synthetic_append_reserves_deleted_orders(
    auth_client, make_grid, state, stub_dispatch_and_billing
):
    grid = make_grid(state)
    config = config_for(grid, 10)
    config.pop("generation_mode")
    config.pop("model")
    response = auth_client.post(
        f"/model-hub/develops/{grid.dataset.id}/add_synthetic_data/",
        {**config, "fill_existing_rows": False},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert_appended(grid, 10)
    stub_dispatch_and_billing.rows.assert_called_once()
    stub_dispatch_and_billing.columns.assert_not_called()


@pytest.mark.parametrize("state", STATES)
def test_synthetic_config_growth_reserves_deleted_orders(
    auth_client, make_grid, state, stub_dispatch_and_billing
):
    grid = make_grid(state)
    config = config_for(grid, 10)
    config.pop("generation_mode")
    config.pop("model")
    grid.dataset.synthetic_dataset_config = deepcopy(config)
    grid.dataset.save(update_fields=["synthetic_dataset_config"])
    live_count = Row.objects.filter(dataset=grid.dataset).count()
    response = auth_client.put(
        f"/model-hub/develops/{grid.dataset.id}/update-synthetic-config/",
        config,
        format="json",
    )
    assert response.status_code == 200, response.data
    assert_appended(grid, 10 - live_count)
    assert Row.objects.filter(dataset=grid.dataset).count() == 10
    stub_dispatch_and_billing.rows.assert_called_once()
    stub_dispatch_and_billing.replace.assert_not_called()


@pytest.mark.parametrize("state", STATES)
def test_blank_append_reserves_deleted_orders(auth_client, make_grid, state):
    grid = make_grid(state)
    response = auth_client.post(
        f"/model-hub/develops/{grid.dataset.id}/add_empty_rows/",
        {"num_rows": 2},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert_appended(grid, 2)


@pytest.mark.parametrize("state", STATES)
def test_paste_append_reserves_deleted_orders(auth_client, make_grid, state):
    grid = make_grid(state)
    # The payload and route used by PasteRowsDialog, also used by SDK row writes.
    response = auth_client.post(
        f"/model-hub/develops/{grid.dataset.id}/add_rows/",
        {
            "rows": [
                {"cells": [{"column_name": "question", "value": value}]}
                for value in ("first", "second")
            ]
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert_appended(grid, 2)


@pytest.mark.parametrize("state", ["tail", "non_monotonic"])
def test_duplicate_rows_reserves_deleted_orders(auth_client, make_grid, state):
    grid = make_grid(state)
    row = Row.objects.get(dataset=grid.dataset)
    response = auth_client.post(
        f"/model-hub/datasets/{grid.dataset.id}/duplicate-rows/",
        {"row_ids": [str(row.id)], "num_copies": 2},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert_appended(grid, 2)


@pytest.mark.parametrize("state", STATES)
def test_file_append_reserves_deleted_orders(auth_client, make_grid, state):
    grid = make_grid(state)
    response = auth_client.post(
        "/model-hub/develops/add_rows_from_file/",
        {
            "dataset_id": str(grid.dataset.id),
            "file": SimpleUploadedFile(
                "append.csv", b"question\nfirst\nsecond\n", content_type="text/csv"
            ),
        },
        format="multipart",
    )
    assert response.status_code == 200, response.data
    assert_appended(grid, 2)


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("route", ["mapped_copy", "merge"])
def test_copy_into_dataset_reserves_deleted_orders(
    auth_client, make_grid, state, route
):
    grid = make_grid(state)
    source = make_grid("source")
    if route == "mapped_copy":
        url = f"/model-hub/develops/{grid.dataset.id}/add_rows_from_existing_dataset/"
        payload = {
            "source_dataset_id": str(source.dataset.id),
            "column_mapping": {str(source.columns[0].id): str(grid.columns[0].id)},
        }
    else:
        url = f"/model-hub/datasets/{source.dataset.id}/merge/"
        payload = {
            "target_dataset_id": str(grid.dataset.id),
            "row_ids": [str(row["id"]) for row in source.before],
        }
    response = auth_client.post(url, payload, format="json")
    assert response.status_code == 200, response.data
    assert_appended(grid, 2)
    assert row_snapshot(source.dataset) == source.before


@pytest.mark.parametrize("state", STATES)
def test_huggingface_append_reserves_deleted_orders(
    auth_client, make_grid, state, monkeypatch
):
    from model_hub.views.datasets.add_rows import huggingface
    from tfc.temporal import drop_in

    grid = make_grid(state)
    monkeypatch.setattr(
        huggingface,
        "load_hf_dataset_with_retries",
        Mock(
            return_value={
                "features": [
                    {"name": "question", "type": {"dtype": "string", "_type": "Value"}}
                ]
            }
        ),
    )
    dispatch = Mock()
    monkeypatch.setattr(drop_in, "start_activity", dispatch)
    response = auth_client.post(
        f"/model-hub/develops/{grid.dataset.id}/add_rows_from_huggingface/",
        {
            "huggingface_dataset_name": "test/questions",
            "huggingface_dataset_config": "default",
            "huggingface_dataset_split": "train",
            "num_rows": 2,
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert_appended(grid, 2)
    dispatch.assert_called_once()


@pytest.mark.parametrize("operation", ["add", "duplicate", "merge", "mapped_copy"])
def test_retained_service_allocators_use_highest_persisted_order(
    make_grid, monkeypatch, operation
):
    from model_hub.services import dataset_service

    grid = make_grid("non_monotonic")
    source = make_grid("source")
    scope = {
        "organization": grid.dataset.organization,
        "workspace": grid.dataset.workspace,
    }
    monkeypatch.setattr(dataset_service, "_check_resource_limit", lambda *a, **k: True)
    if operation == "add":
        result = dataset_service.add_dataset_rows(
            dataset_id=grid.dataset.id,
            rows=[{"question": "first"}, {"question": "second"}],
            **scope,
        )
    elif operation == "duplicate":
        result = dataset_service.duplicate_rows(
            dataset_id=grid.dataset.id,
            row_ids=[Row.objects.get(dataset=grid.dataset).id],
            num_copies=2,
            **scope,
        )
    elif operation == "merge":
        result = dataset_service.merge_datasets(
            source_dataset_id=source.dataset.id,
            target_dataset_id=grid.dataset.id,
            row_ids=[row["id"] for row in source.before],
            **scope,
        )
    else:
        result = dataset_service.add_rows_from_existing(
            source_dataset_id=source.dataset.id,
            target_dataset_id=grid.dataset.id,
            column_mapping={"question": "question"},
            **scope,
        )
    assert isinstance(result, dict), result
    assert_appended(grid, 2)


@pytest.mark.parametrize("state", STATES)
def test_graph_blank_append_reserves_deleted_orders(make_grid, monkeypatch, state):
    from agent_playground.views.dataset_link import GraphDatasetViewSet

    grid = make_grid(state)
    view = GraphDatasetViewSet()
    monkeypatch.setattr(
        view, "get_object", lambda: SimpleNamespace(dataset=grid.dataset)
    )
    response = view.create_row(SimpleNamespace())
    assert response.status_code == 201, response.data
    assert_appended(grid, 1)


@pytest.mark.parametrize("state", STATES)
def test_graph_minimum_row_reserves_deleted_orders(make_grid, state):
    from agent_playground.services.dataset_bridge import _ensure_minimum_row

    grid = make_grid(state)
    # A graph sync can recreate its minimum row after the last live row is deleted.
    now = timezone.now()
    Row.objects.filter(dataset=grid.dataset).update(deleted=True, deleted_at=now)
    Cell.objects.filter(dataset=grid.dataset).update(deleted=True, deleted_at=now)
    grid.before = row_snapshot(grid.dataset)
    grid.cells = cell_snapshot(grid.dataset)
    _ensure_minimum_row(grid.dataset)
    assert_appended(grid, 1)
    _ensure_minimum_row(grid.dataset)
    assert_appended(grid, 1)  # Still a no-op when a live row already exists.


@pytest.mark.parametrize("state", STATES)
def test_persona_append_reserves_deleted_orders(make_grid, state):
    from simulate.services.alk_simulate_ingestion import _append_persona_dataset_rows

    grid = make_grid(state, names=("persona", "situation", "outcome"))
    created = _append_persona_dataset_rows(
        grid.dataset,
        [
            {
                "persona": {"name": "Test persona"},
                "situation": "A question",
                "outcome": "An answer",
            }
        ],
    )
    assert len(created) == 1
    assert_appended(grid, 1)


@pytest.mark.parametrize("state", STATES)
def test_scenario_append_reserves_deleted_orders(
    auth_client, make_grid, state, monkeypatch
):
    from simulate.models import Scenarios
    from simulate.views import scenarios
    from tfc import ee_gating

    grid = make_grid(state)
    scenario = Scenarios.objects.create(
        name="Append order scenario",
        source="test",
        dataset=grid.dataset,
        organization=grid.dataset.organization,
        workspace=grid.dataset.workspace,
    )
    monkeypatch.setattr(ee_gating, "check_ee_feature", Mock())
    dispatch = Mock()
    monkeypatch.setattr(scenarios, "start_add_scenario_rows_workflow_sync", dispatch)
    response = auth_client.post(
        f"/simulate/scenarios/{scenario.id}/add-rows/", {"num_rows": 10}, format="json"
    )
    assert response.status_code == 202, response.data
    assert_appended(grid, 10)
    dispatch.assert_called_once()


@pytest.mark.parametrize("state", STATES)
def test_queue_export_append_reserves_deleted_orders(auth_client, make_grid, state):
    from model_hub.models.annotation_queues import (
        AnnotationQueue,
        AnnotationQueueAnnotator,
        QueueItem,
    )

    grid = make_grid(state)
    source = make_grid("source")
    queue = AnnotationQueue.objects.create(
        name="Append order export",
        organization=grid.dataset.organization,
        workspace=grid.dataset.workspace,
        created_by=grid.dataset.user,
    )
    # Saving a new queue already makes its creator the manager
    # (AnnotationQueue.save); a second active membership would break the
    # unique_active_queue_annotator constraint.
    assert AnnotationQueueAnnotator.objects.filter(
        queue=queue, user=grid.dataset.user, role="manager"
    ).exists()
    for row in source.before:
        # As in the production add path, the item carries its queue's
        # organization (QueueItem.organization is required) and workspace.
        QueueItem.objects.create(
            queue=queue,
            source_type="dataset_row",
            dataset_row_id=row["id"],
            organization=queue.organization,
            workspace=queue.workspace,
            status="completed",
            order=row["order"],
        )
    response = auth_client.post(
        f"/model-hub/annotation-queues/{queue.id}/export-to-dataset/",
        {"dataset_id": str(grid.dataset.id), "status_filter": "completed"},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["result"]["rows_created"] == 2
    # Queue export may backfill newly mapped columns on live existing rows.
    old_cells = {cell["id"]: cell for cell in grid.cells}
    grid.cells = [
        cell
        for cell in cell_snapshot(grid.dataset)
        if cell["row_id"] in {r["id"] for r in grid.before}
    ]
    assert {
        cell["id"]: cell for cell in grid.cells if cell["id"] in old_cells
    } == old_cells
    assert_appended(grid, 2)


@pytest.mark.parametrize("state", STATES)
def test_trace_import_append_reserves_deleted_orders(make_grid, state, monkeypatch):
    from tracer.services.clickhouse import v2
    from tracer.tasks import dataset as dataset_tasks

    grid = make_grid(state)
    spans = [{"id": "span-a", "input": "first"}, {"id": "span-b", "input": "second"}]
    monkeypatch.setattr(v2, "get_reader", lambda: nullcontext(Mock()))
    monkeypatch.setattr(
        dataset_tasks,
        "_export_span_rows",
        lambda *a, **k: {span["id"]: span for span in spans},
    )
    monkeypatch.setattr(dataset_tasks, "close_old_connections", Mock())
    monkeypatch.setattr("tfc.temporal.drop_in.decorator.close_old_connections", Mock())
    # Usage emission is outside the allocator; a local stub handles both builds.
    import sys

    emitter = sys.modules.get("ee.usage.services.emitter")
    if emitter is not None:
        monkeypatch.setattr(emitter, "emit", Mock())
    else:
        monkeypatch.setitem(
            sys.modules, "ee.usage.services.emitter", SimpleNamespace(emit=Mock())
        )
    result = dataset_tasks.process_spans_chunk_task.run_sync(
        [span["id"] for span in spans],
        str(grid.dataset.id),
        [{"column_id": str(grid.columns[0].id), "span_field": "input"}],
        org_id=str(grid.dataset.organization_id),
    )
    assert result == {"rows_created": 2, "cells_created": 2}
    assert_appended(grid, 2)


@pytest.mark.parametrize("existing_count", [20, 10, 5])
def test_append_preserves_rows_columns_and_reviewed_cells(
    auth_client, make_grid, monkeypatch, existing_count
):
    """Port the twin's preservation invariant to head-70's supported append mode."""
    from model_hub.services import row_generation

    grid = make_grid("tail", count=existing_count)
    before_columns = list(
        Column.all_objects.filter(dataset=grid.dataset).order_by("id").values()
    )
    before_order = list(grid.dataset.column_order)
    before_config = deepcopy(grid.dataset.column_config)
    monkeypatch.setattr(
        row_generation, "generate_batch", Mock(return_value=[{"question": "new"}] * 10)
    )
    response = auth_client.post(
        f"/model-hub/develops/{grid.dataset.id}/add_synthetic_data/",
        config_for(grid, 10),
        format="json",
    )
    assert response.status_code == 200, response.data
    assert_appended(grid, 10)
    grid.dataset.refresh_from_db()
    assert (
        list(Column.all_objects.filter(dataset=grid.dataset).order_by("id").values())
        == before_columns
    )
    assert grid.dataset.column_order == before_order
    assert grid.dataset.column_config == before_config
