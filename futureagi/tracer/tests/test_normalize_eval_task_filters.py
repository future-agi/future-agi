"""
Tests for the ``0094_normalize_eval_task_filter_casing`` data migration and
the write-side enforcement of the canonical ``filters`` item contract.

  * camelCase items are rewritten to snake_case keys; values untouched.
  * ``span_attributes_filters`` items fold into ``filters`` and the key drops.
  * Already-canonical tasks are untouched; re-running is a no-op and
    ``updated_at`` is never bumped.
  * Malformed ``filters`` payloads are skipped without crashing.
  * ``EvalTaskFiltersField`` now rejects camelCase items under ``filters``.
"""

import importlib

import pytest
from django.apps import apps as django_apps
from rest_framework import serializers

import model_hub.tasks  # noqa: F401
from tracer.models.eval_task import EvalTask, RunType
from tracer.serializers.filters import eval_task_filters_field

migration = importlib.import_module(
    "tracer.migrations.0094_normalize_eval_task_filter_casing"
)

CAMEL_ITEM = {
    "columnId": "ended_reason",
    "filterConfig": {
        "colType": "SPAN_ATTRIBUTE",
        "filterOp": "not_in",
        "filterType": "text",
        "filterValue": ["voicemail", "silence-timed-out"],
    },
}

SNAKE_ITEM = {
    "column_id": "ended_reason",
    "filter_config": {
        "col_type": "SPAN_ATTRIBUTE",
        "filter_op": "not_in",
        "filter_type": "text",
        "filter_value": ["voicemail", "silence-timed-out"],
    },
}


def _make_task(project, filters):
    return EvalTask.objects.create(
        project=project,
        name="Casing test",
        filters=filters,
        run_type=RunType.HISTORICAL,
    )


def _run_migration():
    migration.forwards(django_apps, None)


@pytest.mark.django_db
class TestForwards:
    def test_camel_case_items_become_snake_case(self, project):
        task = _make_task(
            project,
            {
                "project_id": str(project.id),
                "date_range": ["2026-06-02", "2026-07-02"],
                "filters": [CAMEL_ITEM],
            },
        )
        _run_migration()
        task.refresh_from_db()
        assert task.filters["filters"] == [SNAKE_ITEM]
        assert task.filters["project_id"] == str(project.id)
        assert task.filters["date_range"] == ["2026-06-02", "2026-07-02"]

    def test_span_attributes_filters_fold_into_filters(self, project):
        task = _make_task(
            project, {"span_attributes_filters": [CAMEL_ITEM], "filters": []}
        )
        _run_migration()
        task.refresh_from_db()
        assert task.filters["filters"] == [SNAKE_ITEM]
        assert "span_attributes_filters" not in task.filters

    def test_canonical_task_untouched_and_rerun_is_noop(self, project):
        canonical = {"project_id": str(project.id), "filters": [SNAKE_ITEM]}
        task = _make_task(project, canonical)
        original_updated_at = task.updated_at
        _run_migration()
        _run_migration()
        task.refresh_from_db()
        assert task.filters == canonical
        assert task.updated_at == original_updated_at

    def test_updated_at_preserved_on_rewrite(self, project):
        task = _make_task(project, {"filters": [CAMEL_ITEM]})
        original_updated_at = task.updated_at
        _run_migration()
        task.refresh_from_db()
        assert task.filters["filters"] == [SNAKE_ITEM]
        assert task.updated_at == original_updated_at

    def test_mixed_casing_snake_wins(self, project):
        task = _make_task(project, {"filters": [{**CAMEL_ITEM, "column_id": "kept"}]})
        _run_migration()
        task.refresh_from_db()
        item = task.filters["filters"][0]
        assert item["column_id"] == "kept"
        assert "columnId" not in item

    def test_malformed_filters_are_skipped(self, project):
        malformed = [
            _make_task(project, {"filters": "not-a-list"}),
            _make_task(project, {"": ["junk"]}),
            _make_task(project, {"filters": [None, "junk", 3]}),
        ]
        _run_migration()
        for task in malformed:
            before = task.filters
            task.refresh_from_db()
            assert task.filters == before

    def test_deleted_tasks_are_normalized(self, project):
        task = _make_task(project, {"filters": [CAMEL_ITEM]})
        EvalTask.all_objects.filter(id=task.id).update(deleted=True)
        _run_migration()
        task = EvalTask.all_objects.get(id=task.id)
        assert task.filters["filters"] == [SNAKE_ITEM]


class TestEvalTaskFiltersFieldContract:
    def test_snake_case_filters_accepted(self):
        field = eval_task_filters_field()
        value = field.run_validation({"filters": [SNAKE_ITEM]})
        assert value["filters"] == [SNAKE_ITEM]

    def test_camel_case_filters_rejected(self):
        field = eval_task_filters_field()
        with pytest.raises(serializers.ValidationError):
            field.run_validation({"filters": [CAMEL_ITEM]})
