"""Exercise real recovery DDL and inserts from the previous backend schema."""

import uuid

import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import override_settings

from tracer.tests.test_grouping_runtime import _claimed_runtime


@pytest.mark.django_db(transaction=True)
def test_recovery_migration_preserves_old_inserts_and_rolls_back(
    observe_project, monkeypatch
):
    with override_settings(
        ERROR_FEED_GROUPING_ENABLED=True,
        ERROR_FEED_GROUPING_ALL_PROJECTS=True,
        ERROR_FEED_GROUPING_DEBOUNCE_SECONDS=0,
        ERROR_FEED_GROUPING_BUDGET_ENFORCED=True,
        ERROR_FEED_GROUPING_PROJECT_BUDGET_USD="100",
        ERROR_FEED_GROUPING_WORK_BUDGET_USD="100",
        ERROR_FEED_GROUPING_TENANT_BUDGET_USD="100",
    ):
        _, claim = _claimed_runtime(observe_project, monkeypatch)

    with override_settings(MIGRATION_MODULES={}):
        loader = MigrationLoader(connection)
    migration = loader.get_migration("tracer", "0112_grouping_failure_recovery")
    before = loader.project_state([("tracer", "0111_grouping_budget_wait")])
    old_models = [
        before.apps.get_model("tracer", "TraceGroupingWork"),
        before.apps.get_model("tracer", "TraceGroupingAttempt"),
    ]
    schema = "grouping_recovery_" + uuid.uuid4().hex
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        cursor.execute("SHOW search_path")
        original_search_path = cursor.fetchone()[0]
        cursor.execute("SELECT current_schema()")
        source_schema = cursor.fetchone()[0]
        cursor.execute(f"CREATE SCHEMA {quote(schema)}")

    try:
        with connection.cursor() as cursor:
            for model in old_models:
                table = quote(model._meta.db_table)
                cursor.execute(
                    f"CREATE TABLE {quote(schema)}.{table} "
                    f"(LIKE {quote(source_schema)}.{table} INCLUDING ALL)"
                )
            cursor.execute(
                "SELECT set_config('search_path', %s, false)",
                [f"{quote(schema)}, {original_search_path}"],
            )

        # --nomigrations creates the current tables. Remove only 0112 in the
        # private schema, then run its actual forward and reverse operations.
        with connection.schema_editor() as editor:
            migration.unapply(before, editor)

        def insert_old_columns():
            with connection.cursor() as cursor:
                for model in old_models:
                    table = quote(model._meta.db_table)
                    columns = ", ".join(
                        quote(field.column) for field in model._meta.local_fields
                    )
                    where = (
                        "id = %s"
                        if model._meta.model_name == "tracegroupingattempt"
                        else "id = (SELECT work_id FROM "
                        f"{quote(source_schema)}.tracer_trace_grouping_attempt "
                        "WHERE id = %s)"
                    )
                    cursor.execute(
                        f"INSERT INTO {quote(schema)}.{table} ({columns}) "
                        f"SELECT {columns} FROM {quote(source_schema)}.{table} "
                        f"WHERE {where}",
                        [claim["attempt_id"]],
                    )

        def assert_recovery_defaults():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT failure_code, last_failure_at, retry_start_attempt, "
                    "retry_limit, attempt_number FROM tracer_trace_grouping_work"
                )
                assert cursor.fetchone() == ("", None, 0, 5, 1)
                cursor.execute(
                    "SELECT failure_code, attempt_number, checkpoint FROM "
                    "tracer_trace_grouping_attempt"
                )
                actual = cursor.fetchone()
                cursor.execute(
                    "SELECT '', attempt_number, checkpoint FROM "
                    f"{quote(source_schema)}.tracer_trace_grouping_attempt "
                    "WHERE id = %s",
                    [claim["attempt_id"]],
                )
                assert actual == cursor.fetchone()

        insert_old_columns()
        with connection.schema_editor() as editor:
            migration.apply(before, editor)
        assert_recovery_defaults()

        # Simulate an old ORM that omits the newly added columns after upgrade.
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM tracer_trace_grouping_attempt")
            cursor.execute("DELETE FROM tracer_trace_grouping_work")
        insert_old_columns()
        assert_recovery_defaults()

        with connection.schema_editor() as editor:
            migration.unapply(before, editor)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name IN "
                "('tracer_trace_grouping_work', 'tracer_trace_grouping_attempt')",
                [schema],
            )
            columns = {row[0] for row in cursor.fetchall()}
            assert not columns & {
                "failure_code",
                "last_failure_at",
                "retry_start_attempt",
                "retry_limit",
            }
            cursor.execute("SELECT attempt_number FROM tracer_trace_grouping_work")
            assert cursor.fetchone() == (1,)
            cursor.execute(
                "SELECT attempt_number, checkpoint FROM tracer_trace_grouping_attempt"
            )
            actual = cursor.fetchone()
            cursor.execute(
                "SELECT attempt_number, checkpoint FROM "
                f"{quote(source_schema)}.tracer_trace_grouping_attempt WHERE id = %s",
                [claim["attempt_id"]],
            )
            assert actual == cursor.fetchone()
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('search_path', %s, false)",
                [original_search_path],
            )
            cursor.execute(f"DROP SCHEMA {quote(schema)} CASCADE")
