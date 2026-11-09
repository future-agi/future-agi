"""DB-only additive migration contract; NOT a standalone/offline test.

Run on a disposable fi-infra database for J26 fresh-vs-upgraded comparison.
The executor restores the entry schema even if an assertion fails.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = ("tracer", "0120_merge_sharedlink_and_agent_observe")
AFTER = ("tracer", "0121_background_trace_analysis_state")
TABLE = "tracer_background_trace_analysis_state"


@pytest.mark.django_db(transaction=True)
def test_forward_and_reverse_only_add_remove_lifecycle_table():
    executor = MigrationExecutor(connection)
    restore = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([BEFORE])
        before = set(connection.introspection.table_names())
        assert TABLE not in before
        executor = MigrationExecutor(connection)
        executor.migrate([AFTER])
        after = set(connection.introspection.table_names())
        assert after - before == {TABLE}
        assert before - after == set()
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, TABLE)
        assert any(
            c["unique"] and c["columns"] == ["project_id", "trace_id"]
            for c in constraints.values()
        )
        assert not any(
            c["foreign_key"] and "trace_id" in c["columns"]
            for c in constraints.values()
        )
        executor = MigrationExecutor(connection)
        executor.migrate([BEFORE])
        assert set(connection.introspection.table_names()) == before
    finally:
        MigrationExecutor(connection).migrate(restore)
