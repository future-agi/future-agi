"""Upgrading creates the intent table; it never opts in an existing task."""

import pytest
from django.db import connection
from django.db.migrations import AddField, CreateModel
from django.db.migrations.executor import MigrationExecutor

from tracer.models.eval_task import EvalTask, EvalTaskStartRequest

PREVIOUS = ("tracer", "0137_evaltask_workflow_generation")
CURRENT = ("tracer", "0138_eval_task_start_request")


@pytest.mark.django_db(transaction=True)
def test_forward_migration_leaves_existing_tasks_without_an_intent(eval_task):
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    migration = executor.loader.graph.nodes[CURRENT]
    assert migration.dependencies == [PREVIOUS]
    marker, operation = migration.operations
    assert isinstance(marker, AddField)
    assert marker.name == "instant_paused_before_start_at"
    assert marker.field.null
    assert isinstance(operation, CreateModel)
    assert operation.name == "EvalTaskStartRequest"
    table = EvalTaskStartRequest._meta.db_table
    task = EvalTask.objects.using("default").filter(pk=eval_task.pk)
    before = task.values().get()
    assert before[marker.name] is None
    try:
        executor.migrate([PREVIOUS])
        assert table not in connection.introspection.table_names()
        executor.loader.build_graph()
        executor.migrate([CURRENT])
        assert task.values().get() == before
        assert not EvalTaskStartRequest.objects.using("default").exists()
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, table)
        assert constraints["eval_start_state_lease"]["columns"] == [
            "state",
            "lease_until",
        ]
        assert constraints["eval_start_max_two_attempts"]["check"]
    finally:
        executor.loader.build_graph()
        executor.migrate(targets)
