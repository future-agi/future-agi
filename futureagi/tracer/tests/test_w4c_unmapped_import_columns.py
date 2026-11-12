"""Observe imports leave explicitly unmapped cells blank.

Run the actual worker synchronously with an over-returning span reader and
stubbed persistence. Returning output even when it was not projected ensures
that both the read projection and cell materialization honor the mapping.
"""

import json
import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from model_hub.models import develop_dataset
from model_hub.models.score import Score
from model_hub.services import row_sources
from tracer.models.observation_span import EvalLogger
from tracer.services.clickhouse import v2
from tracer.tasks import dataset as dataset_tasks

pytestmark = pytest.mark.unit

SPAN_ID = "span-unmapped-import"
VIRTUAL_FIELDS = ("child_spans", "eval_metrics", "annotation_metrics")
VIRTUAL_VALUES = {
    "child_spans": [{"id": "child-A", "input": "child question"}],
    "eval_metrics": {
        "quality": {
            "score": 1.0,
            "explanation": "Correct",
            "error": False,
            "error_message": None,
        }
    },
    "annotation_metrics": {"rating": 5},
}


def _mapping(column_name, **source):
    return {"column_id": str(uuid.uuid4()), "column_name": column_name, **source}


@pytest.fixture
def worker(monkeypatch):
    reader = Mock()
    reader.export_fields_by_ids.return_value = {
        SPAN_ID: {"id": SPAN_ID, "input": "question-A", "output": "answer-A"}
    }
    monkeypatch.setattr(v2, "get_reader", lambda: nullcontext(reader))
    monkeypatch.setattr(dataset_tasks, "close_old_connections", Mock())
    monkeypatch.setattr("tfc.temporal.drop_in.decorator.close_old_connections", Mock())
    monkeypatch.setattr(dataset_tasks.transaction, "atomic", nullcontext)
    order_query = Mock()
    order_query.order_by.return_value.values_list.return_value.first.return_value = None
    monkeypatch.setattr(
        develop_dataset.Row.all_objects, "filter", Mock(return_value=order_query)
    )
    rows = Mock(side_effect=lambda values: values)
    cells = Mock()
    monkeypatch.setattr(develop_dataset.Row.objects, "bulk_create", rows)
    monkeypatch.setattr(develop_dataset.Cell.objects, "bulk_create", cells)
    monkeypatch.setattr(row_sources, "record_row_sources", Mock())

    child_loader = Mock(return_value={SPAN_ID: VIRTUAL_VALUES["child_spans"]})
    monkeypatch.setattr(dataset_tasks, "_serialize_span_trees_batch", child_loader)
    eval_filter = Mock()
    eval_filter.return_value.order_by.return_value = [
        SimpleNamespace(
            observation_span_id=SPAN_ID,
            eval_type_id="quality",
            id="eval-A",
            output_float=1.0,
            results_explanation="Correct",
            error=False,
            error_message=None,
        )
    ]
    monkeypatch.setattr(EvalLogger.objects, "filter", eval_filter)
    annotation_filter = Mock()
    annotation_filter.return_value.select_related.return_value.order_by.return_value = [
        SimpleNamespace(
            observation_span_id=SPAN_ID,
            label=SimpleNamespace(name="rating"),
            value=5,
        )
    ]
    monkeypatch.setattr(Score.objects, "filter", annotation_filter)

    def run(mapping):
        result = dataset_tasks.process_spans_chunk_task.run_sync(
            [SPAN_ID],
            str(uuid.uuid4()),
            mapping,
            project_id="project-A",
            org_id="org-A",
        )
        assert result == {"rows_created": 1, "cells_created": len(mapping)}
        rows.assert_called_once()
        cells.assert_called_once()
        saved = cells.call_args.args[0]
        assert len(saved) == len(mapping)
        assert all(cell.row is rows.call_args.args[0][0] for cell in saved)
        return {str(cell.column_id): cell.value for cell in saved}

    return SimpleNamespace(
        run=run,
        reader=reader,
        loaders={
            "child_spans": child_loader,
            "eval_metrics": eval_filter,
            "annotation_metrics": annotation_filter,
        },
    )


@pytest.mark.parametrize("assertion", ["cell", "projection"])
def test_worker_leaves_explicitly_unmapped_output_blank(worker, assertion):
    # DatasetView appends this explicit None for each omitted existing column.
    mapping = [
        _mapping("input", span_field="input"),
        _mapping("output", span_field=None),
    ]
    values = worker.run(mapping)

    assert values[mapping[0]["column_id"]] == "question-A"
    if assertion == "cell":
        assert values[mapping[1]["column_id"]] is None
    else:
        worker.reader.export_fields_by_ids.assert_called_once_with(
            [SPAN_ID], {"input"}, project_id="project-A", org_id="org-A"
        )


@pytest.mark.parametrize("column_name", ["output", "answer"])
def test_worker_preserves_explicit_output_mapping(worker, column_name):
    mapping = [
        _mapping("input", span_field="input"),
        _mapping(column_name, span_field="output"),
    ]
    values = worker.run(mapping)

    assert values[mapping[0]["column_id"]] == "question-A"
    assert values[mapping[1]["column_id"]] == "answer-A"
    worker.reader.export_fields_by_ids.assert_called_once_with(
        [SPAN_ID], {"input", "output"}, project_id="project-A", org_id="org-A"
    )


def test_worker_omitted_legacy_source_key_uses_column_name(worker):
    mapping = [_mapping("input", span_field="input"), _mapping("output")]
    values = worker.run(mapping)

    assert values[mapping[0]["column_id"]] == "question-A"
    assert values[mapping[1]["column_id"]] == "answer-A"
    worker.reader.export_fields_by_ids.assert_called_once_with(
        [SPAN_ID], {"input", "output"}, project_id="project-A", org_id="org-A"
    )


@pytest.mark.parametrize("field_name", VIRTUAL_FIELDS)
def test_worker_does_not_load_unmapped_virtual_fields(worker, field_name):
    mapping = [
        _mapping("input", span_field="input"),
        _mapping(field_name, span_field=None),
    ]
    values = worker.run(mapping)

    assert values[mapping[0]["column_id"]] == "question-A"
    assert values[mapping[1]["column_id"]] is None
    for loader in worker.loaders.values():
        loader.assert_not_called()
    worker.reader.export_fields_by_ids.assert_called_once_with(
        [SPAN_ID], {"input"}, project_id="project-A", org_id="org-A"
    )


@pytest.mark.parametrize("field_name", VIRTUAL_FIELDS)
@pytest.mark.parametrize("mapping_kind", ["renamed", "legacy"])
def test_worker_preserves_virtual_mappings(worker, field_name, mapping_kind):
    destination = (
        _mapping("details", span_field=field_name)
        if mapping_kind == "renamed"
        else _mapping(field_name)
    )
    mapping = [_mapping("input", span_field="input"), destination]
    values = worker.run(mapping)

    assert values[mapping[0]["column_id"]] == "question-A"
    assert json.loads(values[destination["column_id"]]) == VIRTUAL_VALUES[field_name]
    for name, loader in worker.loaders.items():
        assert loader.call_count == (1 if name == field_name else 0)
    worker.reader.export_fields_by_ids.assert_called_once_with(
        [SPAN_ID], {"input"}, project_id="project-A", org_id="org-A"
    )


def test_worker_can_leave_all_destination_cells_blank(worker):
    mapping = [
        _mapping("input", span_field=None),
        _mapping("output", span_field=None),
    ]
    values = worker.run(mapping)

    assert values == {item["column_id"]: None for item in mapping}
    worker.reader.export_fields_by_ids.assert_called_once_with(
        [SPAN_ID], set(), project_id="project-A", org_id="org-A"
    )
