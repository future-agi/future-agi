"""Upgrading creates the intent table; it never opts in an existing task."""

import pytest
from django.db import connection
from django.db.migrations import CreateModel
from django.db.migrations.executor import MigrationExecutor

from tracer.models.eval_task import EvalTask, EvalTaskStartRequest, InstantEvalResume

PREVIOUS = ("tracer", "0135_eval_put_back")
CURRENT = ("tracer", "0136_eval_task_start_request")


@pytest.mark.django_db(transaction=True)
def test_forward_migration_leaves_existing_tasks_without_an_intent(eval_task):
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    migration = executor.loader.graph.nodes[CURRENT]
    assert migration.dependencies == [PREVIOUS]
    assert all(isinstance(op, CreateModel) for op in migration.operations)
    assert [op.name for op in migration.operations] == [
        "EvalTaskStartRequest",
        "InstantEvalResume",
    ]
    table = EvalTaskStartRequest._meta.db_table
    task = EvalTask.objects.using("default").filter(pk=eval_task.pk)
    before = task.values().get()
    try:
        executor.migrate([PREVIOUS])
        tables = connection.introspection.table_names()
        assert table not in tables and InstantEvalResume._meta.db_table not in tables
        executor.loader.build_graph()
        executor.migrate([CURRENT])
        assert task.values().get() == before
        assert not EvalTaskStartRequest.objects.using("default").exists()
        assert not InstantEvalResume.objects.using("default").exists()
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
