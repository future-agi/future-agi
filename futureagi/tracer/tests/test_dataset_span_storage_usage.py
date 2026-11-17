"""Span imports meter stored cell bytes without making metering a row failure."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from ee.usage.schemas.events import UsageEvent
from model_hub.models import develop_dataset
from model_hub.services import row_sources
from tracer.services.clickhouse import v2
from tracer.tasks import dataset as dataset_tasks

pytestmark = pytest.mark.unit

DATASET_ID = UUID("00000000-0000-0000-0000-000000000101")
DATASET_ORG_ID = UUID("00000000-0000-0000-0000-000000000102")
CALLER_ORG_ID = UUID("00000000-0000-0000-0000-000000000103")
SPAN_IDS = ["span-storage-a", "span-storage-b"]


@pytest.fixture
def worker(monkeypatch):
    reader = Mock()
    reader.export_fields_by_ids.return_value = {
        SPAN_IDS[0]: {"id": SPAN_IDS[0], "input": "café", "output": {"ok": True}},
        SPAN_IDS[1]: {"id": SPAN_IDS[1], "input": "", "output": 0},
    }
    monkeypatch.setattr(v2, "get_reader", lambda: nullcontext(reader))
    monkeypatch.setattr(dataset_tasks, "close_old_connections", Mock())
    monkeypatch.setattr("tfc.temporal.drop_in.decorator.close_old_connections", Mock())
    monkeypatch.setattr(dataset_tasks.transaction, "atomic", nullcontext)
    order_query = Mock()
    order_query.order_by.return_value.values_list.return_value.first.return_value = 6
    monkeypatch.setattr(
        develop_dataset.Row.all_objects, "filter", Mock(return_value=order_query)
    )
    rows = Mock(side_effect=lambda values: values)
    cells = Mock()
    monkeypatch.setattr(develop_dataset.Row.objects, "bulk_create", rows)
    monkeypatch.setattr(develop_dataset.Cell.objects, "bulk_create", cells)
    monkeypatch.setattr(row_sources, "record_row_sources", Mock())

    dataset_query = Mock()
    dataset_query.get.return_value = SimpleNamespace(organization_id=DATASET_ORG_ID)
    dataset_only = Mock(return_value=dataset_query)
    monkeypatch.setattr(develop_dataset.Dataset.objects, "only", dataset_only)
    emit = Mock()
    # The worker imports emit inside the call; patch that source, not a task attr.
    monkeypatch.setattr("ee.usage.services.emitter.emit", emit)
    log = Mock()
    monkeypatch.setattr(dataset_tasks, "logger", log)
    mapping = [
        {
            "column_id": str(UUID(int=index + 1)),
            "column_name": name,
            "span_field": source,
        }
        for index, (name, source) in enumerate(
            [("input", "input"), ("output", "output"), ("blank", None)]
        )
    ]

    def run():
        return dataset_tasks.process_spans_chunk_task.run_sync(
            SPAN_IDS,
            str(DATASET_ID),
            mapping,
            project_id="project-storage",
            org_id=str(CALLER_ORG_ID),
        )

    return SimpleNamespace(
        run=run,
        rows=rows,
        cells=cells,
        dataset_only=dataset_only,
        dataset_query=dataset_query,
        emit=emit,
        log=log,
    )


def test_span_rows_emit_one_storage_usage_event(worker):
    assert worker.run() == {"rows_created": 2, "cells_created": 6}
    worker.rows.assert_called_once()
    worker.cells.assert_called_once()
    assert worker.cells.call_args.kwargs == {"batch_size": 1000}
    assert [cell.value for cell in worker.cells.call_args.args[0]] == [
        "café",
        '{"ok": true}',
        None,
        "",
        "0",
        None,
    ]

    # This is RED on the bad Database import: creation succeeds, emit never runs.
    worker.emit.assert_called_once()
    assert worker.emit.call_args.kwargs == {}
    (event,) = worker.emit.call_args.args
    assert isinstance(event, UsageEvent)
    assert event.org_id == str(DATASET_ORG_ID)
    assert event.org_id != str(CALLER_ORG_ID)
    assert event.event_type == "dataset_row_from_spans"
    # Five UTF-8 bytes for café, twelve for the JSON object, one for zero.
    assert event.amount == 18
    assert event.properties == {
        "source": "dataset_from_spans",
        "source_id": str(DATASET_ID),
        "rows_created": 2,
    }
    worker.dataset_only.assert_called_once_with("organization_id")
    worker.dataset_query.get.assert_called_once_with(id=str(DATASET_ID))
    worker.log.warning.assert_not_called()


@pytest.mark.parametrize("failure_point", ["dataset_lookup", "emitter"])
def test_metering_failure_warns_without_failing_row_creation(worker, failure_point):
    if failure_point == "dataset_lookup":
        worker.dataset_query.get.side_effect = RuntimeError("lookup unavailable")
    else:
        worker.emit.side_effect = RuntimeError("emitter unavailable")

    assert worker.run() == {"rows_created": 2, "cells_created": 6}
    worker.rows.assert_called_once()
    worker.cells.assert_called_once()
    worker.log.warning.assert_called_once_with(
        "emit_dataset_storage_event_failed", dataset_id=str(DATASET_ID)
    )
    worker.dataset_only.assert_called_once_with("organization_id")
    worker.dataset_query.get.assert_called_once_with(id=str(DATASET_ID))
    if failure_point == "dataset_lookup":
        worker.emit.assert_not_called()
    else:
        worker.emit.assert_called_once()
    worker.log.exception.assert_not_called()
